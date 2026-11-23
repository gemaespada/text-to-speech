"""FastAPI application entry point for the Text to Speech web backend."""

from contextlib import asynccontextmanager, contextmanager, suppress
import fcntl
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from typing import Any, AsyncIterator, Callable, IO, Iterator, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.types import Receive, Scope, Send

from backend.schemas import (
    HealthResponse,
    LanguageItem,
    LanguagesResponse,
    LANGUAGE_NAMES,
    TTSRequest,
)
from backend.security import (
    ArticleTooLargeError,
    HTMLTooLargeError,
    InMemoryRateLimiter,
    MAX_HTML_BYTES,
    RateLimitExceededError,
    SSRFError,
    extract_client_ip,
    validate_article_length,
    validate_security_config,
)
from src.converter import DEFAULT_LANGUAGE, SUPPORTED_VOICES, text_to_speech
from src.extractor import ensure_nltk_resources, extract_article, prepare_nltk_resources

logger = logging.getLogger("tts_backend")

DEFAULT_FRONTEND_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
TEMP_DIR_PREFIX = "tts_req_"
LOCK_FILENAME = ".active.lock"
LIFECYCLE_COORD_LOCK_FILENAME = ".tts_req_lifecycle.lock"
STALE_CLEANUP_INTERVAL_SECONDS: float = 60.0

_STALE_CLEANUP_LOCK = threading.Lock()
_LAST_STALE_CLEANUP: Optional[float] = None


def get_allowed_origins() -> list[str]:
    """Parse allowed CORS origins from FRONTEND_ORIGINS environment variable."""
    raw_origins = os.getenv("FRONTEND_ORIGINS", DEFAULT_FRONTEND_ORIGINS)
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


def extract_safe_hostname(url: str) -> str:
    """Return only the hostname from a URL, omitting credentials, paths, and query parameters."""
    try:
        hostname = urlparse(url).hostname
        return (hostname or "<unknown-host>").lower()
    except ValueError:
        return "<invalid-url>"


@contextmanager
def _lifecycle_coordination_lock(
    root: Path,
    exclusive: bool,
) -> Iterator[None]:
    """Acquire an OS-level shared or exclusive lock coordinating temp dir creation and cleanup.

    - create_locked_temp_dir() acquires a shared lock (LOCK_SH) while creating the directory
      and acquiring its per-directory .active.lock (LOCK_EX), allowing concurrent creations.
    - cleanup_stale_temp_dirs() acquires an exclusive lock (LOCK_EX) during its scan,
      guaranteeing that no live directory can ever be observed in the middle of creation
      before its .active.lock is established.
    """
    coord_path = root / LIFECYCLE_COORD_LOCK_FILENAME
    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    with open(coord_path, "a+b") as coord_file:
        fcntl.flock(coord_file.fileno(), mode)
        try:
            yield
        finally:
            with suppress(OSError):
                fcntl.flock(coord_file.fileno(), fcntl.LOCK_UN)


def create_locked_temp_dir(
    temp_root: Optional[Path] = None,
) -> tuple[Path, IO[bytes]]:
    """Create a per-request temporary directory protected by an OS-level exclusive flock.

    Synchronized with _lifecycle_coordination_lock(exclusive=False) so that
    cleanup_stale_temp_dirs() can never observe the new directory before its
    .active.lock file is created and locked.
    """
    root = temp_root or Path(tempfile.gettempdir())
    with _lifecycle_coordination_lock(root, exclusive=False):
        temp_dir = Path(tempfile.mkdtemp(prefix=TEMP_DIR_PREFIX, dir=str(root)))
        lock_path = temp_dir / LOCK_FILENAME
        lock_file = open(lock_path, "a+b")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            with suppress(OSError):
                lock_file.close()
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        return temp_dir, lock_file


def cleanup_temp_dir(
    temp_dir: Optional[Path],
    lock_file: Optional[IO[bytes]] = None,
) -> None:
    """Idempotently remove a request temporary directory and release its OS lock descriptor."""
    try:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
    finally:
        if lock_file is not None and not lock_file.closed:
            with suppress(OSError):
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            with suppress(OSError):
                lock_file.close()


def make_once_cleanup(
    temp_dir: Optional[Path],
    lock_file: Optional[IO[bytes]] = None,
) -> Callable[[], None]:
    """Return a thread-safe callable that executes cleanup_temp_dir at most once."""
    guard_lock = threading.Lock()
    executed = False

    def _cleanup_once() -> None:
        nonlocal executed
        with guard_lock:
            if executed:
                return
            executed = True
        cleanup_temp_dir(temp_dir, lock_file)

    return _cleanup_once


class SafeFileResponse(FileResponse):
    """FileResponse subclass that guarantees cleanup even when the client disconnects mid-stream.

    In Starlette's standard FileResponse.__call__, `await self.background()` is placed
    after `await self._handle_simple(...)` without a `try / finally:` block. If the
    client disconnects while receiving chunks (causing `send()` to raise OSError or
    asyncio.CancelledError), Starlette skips `self.background()`. SafeFileResponse wraps
    the ASGI transmission in `try / finally:` and invokes the once-only cleanup callback
    synchronously when transmission finishes or is interrupted.
    """

    def __init__(
        self,
        *args: Any,
        cleanup_callback: Optional[Callable[[], None]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._cleanup_callback = cleanup_callback

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self._cleanup_callback is not None:
                self._cleanup_callback()


def cleanup_stale_temp_dirs(
    temp_root: Optional[Path] = None,
) -> int:
    """Remove abandoned tts_req_* directories that are not locked by any active request.

    Acquires an exclusive coordination lock (_lifecycle_coordination_lock) so no
    directory creation is mid-flight, then probes each candidate's .active.lock
    with a non-blocking exclusive flock (LOCK_EX | LOCK_NB). Active directories
    (across threads or processes, regardless of duration) fail the probe and are
    preserved; abandoned directories from crashed processes succeed and are removed.
    """
    root = temp_root or Path(tempfile.gettempdir())
    removed_count = 0

    with _lifecycle_coordination_lock(root, exclusive=True):
        for candidate in root.glob(f"{TEMP_DIR_PREFIX}*"):
            try:
                if not candidate.is_dir():
                    continue

                lock_path = candidate / LOCK_FILENAME
                if not lock_path.exists():
                    shutil.rmtree(candidate, ignore_errors=True)
                    removed_count += 1
                    continue

                with open(lock_path, "a+b") as probe_file:
                    try:
                        fcntl.flock(probe_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except (BlockingIOError, OSError):
                        # Directory is actively held by a live thread or process.
                        continue

                    try:
                        shutil.rmtree(candidate, ignore_errors=True)
                        removed_count += 1
                    finally:
                        with suppress(OSError):
                            fcntl.flock(probe_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                continue

    return removed_count


def _maybe_cleanup_stale_temp_dirs() -> None:
    """Synchronized periodic cleanup preventing concurrent duplicate maintenance runs."""
    global _LAST_STALE_CLEANUP
    if not _STALE_CLEANUP_LOCK.acquire(blocking=False):
        return
    try:
        now = time.monotonic()
        if (
            _LAST_STALE_CLEANUP is None
            or (now - _LAST_STALE_CLEANUP) >= STALE_CLEANUP_INTERVAL_SECONDS
        ):
            _LAST_STALE_CLEANUP = now
            cleanup_stale_temp_dirs()
    finally:
        _STALE_CLEANUP_LOCK.release()


def get_client_ip(request: Request) -> str:
    """Determine the client IP address safely without trusting unverified headers."""
    peer_host = request.client.host if request.client else None
    return extract_client_ip(peer_host=peer_host, headers=request.headers)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Validate security configuration, prepare NLTK data, and clean stale temp dirs on startup."""
    validate_security_config()
    prepare_nltk_resources()
    ensure_nltk_resources()
    cleanup_stale_temp_dirs()
    yield


app = FastAPI(
    title="Text to Speech API",
    description="Backend API that extracts web articles and converts them into MP3 audio using Edge TTS.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
    expose_headers=["Content-Disposition"],
)

rate_limiter = InMemoryRateLimiter()


@app.get("/api/health", response_model=HealthResponse, tags=["System"])
def health_check() -> HealthResponse:
    """Return service health status."""
    return HealthResponse(status="ok")


@app.get("/api/languages", response_model=LanguagesResponse, tags=["TTS"])
def list_languages() -> LanguagesResponse:
    """Return the list of supported synthesis languages and their assigned neural voices."""
    items = [
        LanguageItem(
            code=code,
            name=LANGUAGE_NAMES.get(code, code.upper()),
            voice=voice,
        )
        for code, voice in SUPPORTED_VOICES.items()
    ]
    return LanguagesResponse(default=DEFAULT_LANGUAGE, languages=items)


@app.post(
    "/api/tts",
    response_class=FileResponse,
    tags=["TTS"],
    responses={
        200: {"content": {"audio/mpeg": {}}},
        400: {"description": "Invalid URL, SSRF violation, or no extractable text."},
        413: {"description": "Downloaded HTML or extracted article text exceeds size limits."},
        429: {"description": "Rate limit exceeded."},
        502: {"description": "Upstream article download or Edge TTS synthesis failed."},
        500: {"description": "Internal processing or file system error."},
    },
)
def convert_article_to_mp3(payload: TTSRequest, request: Request) -> FileResponse:
    """Extract a web article from a URL, convert it to MP3, and stream the audio file."""
    _maybe_cleanup_stale_temp_dirs()

    client_ip = get_client_ip(request)
    try:
        rate_limiter.check(client_ip)
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc

    safe_host = extract_safe_hostname(payload.url)
    temp_dir: Optional[Path] = None
    lock_file: Optional[IO[bytes]] = None
    cleanup_once: Optional[Callable[[], None]] = None
    ownership_transferred = False

    try:
        article_text = extract_article(payload.url)
        validate_article_length(article_text)

        temp_dir, lock_file = create_locked_temp_dir()
        cleanup_once = make_once_cleanup(temp_dir, lock_file)
        temp_mp3_path = temp_dir / "article.mp3"

        saved_path = text_to_speech(
            text=article_text,
            output_path=str(temp_mp3_path),
            language=payload.language,
        )

        response = SafeFileResponse(
            path=saved_path,
            media_type="audio/mpeg",
            filename="article.mp3",
            background=BackgroundTask(cleanup_once),
            cleanup_callback=cleanup_once,
        )
        ownership_transferred = True
        return response
    except HTMLTooLargeError as exc:
        logger.warning(
            "HTML size limit exceeded for host '%s' (%s)",
            safe_host,
            type(exc).__name__,
        )
        max_mb = MAX_HTML_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"The article web page exceeds the maximum allowed download size of {max_mb} MB.",
        ) from exc
    except ArticleTooLargeError as exc:
        logger.warning(
            "Article text length limit exceeded for host '%s' (%s)",
            safe_host,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except SSRFError as exc:
        logger.warning(
            "Blocked restricted URL for host '%s' (%s)",
            safe_host,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided URL is not allowed. Only public HTTP and HTTPS web addresses are permitted.",
        ) from exc
    except (ValueError, TypeError) as exc:
        logger.info(
            "Validation error for host '%s' (%s)",
            safe_host,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The article could not be processed. Please verify the URL and ensure the page contains readable text.",
        ) from exc
    except ConnectionError as exc:
        logger.warning(
            "Upstream connection error for host '%s' (%s)",
            safe_host,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Could not download the article or reach the speech synthesis service. "
                "Please verify that the URL is publicly accessible and try again."
            ),
        ) from exc
    except (RuntimeError, OSError) as exc:
        logger.error(
            "Internal processing error for host '%s' (%s)",
            safe_host,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while generating the audio file.",
        ) from exc
    except Exception as exc:
        logger.error(
            "Unexpected internal error for host '%s' (%s)",
            safe_host,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while generating the audio file.",
        ) from exc
    finally:
        if not ownership_transferred:
            if cleanup_once is not None:
                cleanup_once()
            elif temp_dir is not None or lock_file is not None:
                cleanup_temp_dir(temp_dir, lock_file)
