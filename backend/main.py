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

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.types import Receive, Scope, Send

from backend.cache import ConversionCache, build_url_cache_key
from backend.schemas import (
    EngineCatalog,
    HealthResponse,
    LanguageItem,
    LanguagesResponse,
    LANGUAGE_NAMES,
    TextTTSRequest,
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
from src.converter import (
    DEFAULT_ENGINE,
    DEFAULT_LANGUAGE,
    ElevenLabsError,
    SUPPORTED_VOICES,
    get_engines_catalog,
    text_to_speech,
    validate_engine,
    validate_engine_voice,
    validate_language,
)
from src.document_extractor import (
    DocumentTooLargeError,
    DocumentValidationError,
    EmptyDocumentError,
    MAX_UPLOAD_FILE_BYTES,
    extract_text_from_document,
)
from src.extractor import ensure_nltk_resources, extract_article, prepare_nltk_resources

logger = logging.getLogger("tts_backend")

DEFAULT_FRONTEND_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
TEMP_DIR_PREFIX = "tts_req_"
LOCK_FILENAME = ".active.lock"
LIFECYCLE_COORD_LOCK_FILENAME = ".tts_req_lifecycle.lock"
STALE_CLEANUP_INTERVAL_SECONDS: float = 60.0
UPLOAD_READ_CHUNK_BYTES: int = 64 * 1024

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
    """FileResponse subclass that guarantees cleanup even when the client disconnects mid-stream."""

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
    """Remove abandoned tts_req_* directories that are not locked by any active request."""
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


conversion_cache = ConversionCache()


class _AppRateLimiter(InMemoryRateLimiter):
    """InMemoryRateLimiter that also clears the conversion cache on test reset()."""

    def reset(self) -> None:
        super().reset()
        conversion_cache.clear()


rate_limiter = _AppRateLimiter()


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
            conversion_cache.evict_expired()
    finally:
        _STALE_CLEANUP_LOCK.release()


def get_client_ip(request: Request) -> str:
    """Determine the client IP address safely without trusting unverified headers."""
    peer_host = request.client.host if request.client else None
    return extract_client_ip(peer_host=peer_host, headers=request.headers)


def _check_rate_limit(request: Request) -> None:
    """Enforce per-IP rate limiting or raise HTTP 429."""
    client_ip = get_client_ip(request)
    try:
        rate_limiter.check(client_ip)
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc


def _invoke_tts_engine(
    text: str,
    output_path: str,
    language: str,
    engine: str,
    voice: Optional[str],
) -> str:
    """Call text_to_speech preserving 3-argument signature compatibility for default Edge TTS."""
    if engine == DEFAULT_ENGINE and (
        voice is None or voice == SUPPORTED_VOICES.get(language)
    ):
        return text_to_speech(
            text=text,
            output_path=output_path,
            language=language,
        )
    return text_to_speech(
        text=text,
        output_path=output_path,
        language=language,
        engine=engine,
        voice=voice,
    )


def _try_serve_cached_url_conversion(cache_key: str) -> Optional[SafeFileResponse]:
    """If cache_key is present in conversion_cache, copy to a locked temp dir and return SafeFileResponse."""
    if not conversion_cache.contains(cache_key):
        return None

    temp_dir: Optional[Path] = None
    lock_file: Optional[IO[bytes]] = None
    cleanup_once: Optional[Callable[[], None]] = None
    ownership_transferred = False

    try:
        temp_dir, lock_file = create_locked_temp_dir()
        cleanup_once = make_once_cleanup(temp_dir, lock_file)
        temp_mp3_path = temp_dir / "article.mp3"

        if not conversion_cache.copy_to_destination(cache_key, temp_mp3_path):
            return None

        response = SafeFileResponse(
            path=str(temp_mp3_path),
            media_type="audio/mpeg",
            filename="article.mp3",
            headers={"X-Cache": "HIT"},
            background=BackgroundTask(cleanup_once),
            cleanup_callback=cleanup_once,
        )
        ownership_transferred = True
        return response
    except Exception:
        return None
    finally:
        if not ownership_transferred:
            if cleanup_once is not None:
                cleanup_once()
            elif temp_dir is not None or lock_file is not None:
                cleanup_temp_dir(temp_dir, lock_file)


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
    description=(
        "Backend API that extracts web articles, manual texts, or local documents "
        "and converts them into MP3 audio using Edge TTS or ElevenLabs (eleven_v4)."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
    expose_headers=["Content-Disposition", "X-Cache"],
)


@app.get("/api/health", response_model=HealthResponse, tags=["System"])
def health_check() -> HealthResponse:
    """Return service health status."""
    return HealthResponse(status="ok")


@app.get("/api/languages", response_model=LanguagesResponse, tags=["TTS"])
def list_languages() -> LanguagesResponse:
    """Return the list of supported synthesis languages, engines, and their assigned neural voices."""
    items = [
        LanguageItem(
            code=code,
            name=LANGUAGE_NAMES.get(code, code.upper()),
            voice=voice,
        )
        for code, voice in SUPPORTED_VOICES.items()
    ]
    engines = [
        EngineCatalog(**engine_data)
        for engine_data in get_engines_catalog(LANGUAGE_NAMES)
    ]
    return LanguagesResponse(
        default=DEFAULT_LANGUAGE,
        languages=items,
        default_engine=DEFAULT_ENGINE,
        engines=engines,
    )


@app.post(
    "/api/tts",
    response_class=FileResponse,
    tags=["TTS"],
    responses={
        200: {"content": {"audio/mpeg": {}}},
        400: {"description": "Invalid URL, SSRF violation, or no extractable text."},
        413: {"description": "Downloaded HTML or extracted article text exceeds size limits."},
        429: {"description": "Rate limit exceeded."},
        502: {"description": "Upstream article download or TTS synthesis failed."},
        500: {"description": "Internal processing or file system error."},
    },
)
def convert_article_to_mp3(payload: TTSRequest, request: Request) -> FileResponse:
    """Extract a web article from a URL, convert it to MP3 (with URL caching), and stream the audio."""
    _maybe_cleanup_stale_temp_dirs()
    _check_rate_limit(request)

    safe_host = extract_safe_hostname(payload.url)
    resolved_voice = validate_engine_voice(
        engine=payload.engine,
        language=payload.language,
        voice=payload.voice,
    )
    cache_key = build_url_cache_key(
        url=payload.url,
        engine=payload.engine,
        language=payload.language,
        voice=resolved_voice,
    )

    cached_response = _try_serve_cached_url_conversion(cache_key)
    if cached_response is not None:
        return cached_response

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

        saved_path = _invoke_tts_engine(
            text=article_text,
            output_path=str(temp_mp3_path),
            language=payload.language,
            engine=payload.engine,
            voice=payload.voice,
        )

        conversion_cache.put_from_file(cache_key, Path(saved_path))

        response = SafeFileResponse(
            path=saved_path,
            media_type="audio/mpeg",
            filename="article.mp3",
            headers={"X-Cache": "MISS"},
            background=BackgroundTask(cleanup_once),
            cleanup_callback=cleanup_once,
        )
        ownership_transferred = True
        return response
    except ElevenLabsError as exc:
        logger.warning(
            "ElevenLabs synthesis error for host '%s' (code=%s, status=%d)",
            safe_host,
            exc.error_code,
            exc.status_code,
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.user_message,
        ) from exc
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


@app.post(
    "/api/tts/text",
    response_class=FileResponse,
    tags=["TTS"],
    responses={
        200: {"content": {"audio/mpeg": {}}},
        400: {"description": "Empty or invalid manual text."},
        413: {"description": "Text exceeds the 15,000-character limit."},
        429: {"description": "Rate limit exceeded."},
        502: {"description": "Speech synthesis service failed."},
        500: {"description": "Internal processing or file system error."},
    },
)
def convert_text_to_mp3(payload: TextTTSRequest, request: Request) -> FileResponse:
    """Convert manually provided plain text into MP3 audio without persistent caching."""
    _maybe_cleanup_stale_temp_dirs()
    _check_rate_limit(request)

    temp_dir: Optional[Path] = None
    lock_file: Optional[IO[bytes]] = None
    cleanup_once: Optional[Callable[[], None]] = None
    ownership_transferred = False

    try:
        validate_article_length(payload.text)

        temp_dir, lock_file = create_locked_temp_dir()
        cleanup_once = make_once_cleanup(temp_dir, lock_file)
        temp_mp3_path = temp_dir / "article.mp3"

        saved_path = _invoke_tts_engine(
            text=payload.text,
            output_path=str(temp_mp3_path),
            language=payload.language,
            engine=payload.engine,
            voice=payload.voice,
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
    except ElevenLabsError as exc:
        logger.warning(
            "ElevenLabs synthesis error for manual text (code=%s, status=%d)",
            exc.error_code,
            exc.status_code,
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.user_message,
        ) from exc
    except ArticleTooLargeError as exc:
        logger.warning("Manual text length limit exceeded (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except (ValueError, TypeError) as exc:
        logger.info("Validation error for manual text (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ConnectionError as exc:
        logger.warning("Upstream TTS connection error for manual text (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the speech synthesis service. Please try again.",
        ) from exc
    except (RuntimeError, OSError) as exc:
        logger.error("Internal processing error for manual text (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while generating the audio file.",
        ) from exc
    except Exception as exc:
        logger.error("Unexpected internal error for manual text (%s)", type(exc).__name__)
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


def _read_bounded_upload_bytes(
    upload_file: UploadFile,
    max_bytes: int = MAX_UPLOAD_FILE_BYTES,
) -> bytes:
    """Read an uploaded file in chunks enforcing max_bytes without exhausting memory."""
    buffer = bytearray()
    while True:
        chunk = upload_file.file.read(UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            max_mb = max_bytes // (1024 * 1024)
            raise DocumentTooLargeError(
                f"El archivo supera el tamaño máximo permitido de {max_mb} MB."
            )
    return bytes(buffer)


@app.post(
    "/api/tts/file",
    response_class=FileResponse,
    tags=["TTS"],
    responses={
        200: {"content": {"audio/mpeg": {}}},
        400: {"description": "Invalid, empty, corrupted, or unsupported document file."},
        413: {"description": "Uploaded file or extracted text exceeds size limits."},
        429: {"description": "Rate limit exceeded."},
        502: {"description": "Speech synthesis service failed."},
        500: {"description": "Internal processing or file system error."},
    },
)
def convert_file_to_mp3(
    request: Request,
    file: UploadFile = File(...),
    language: str = Form(DEFAULT_LANGUAGE),
    engine: str = Form(DEFAULT_ENGINE),
    voice: Optional[str] = Form(None),
) -> FileResponse:
    """Extract text from an uploaded PDF, DOCX, TXT, or EPUB file and convert it to MP3."""
    _maybe_cleanup_stale_temp_dirs()
    _check_rate_limit(request)

    temp_dir: Optional[Path] = None
    lock_file: Optional[IO[bytes]] = None
    cleanup_once: Optional[Callable[[], None]] = None
    ownership_transferred = False

    try:
        cleaned_engine = validate_engine(engine)
        validate_language(language)
        cleaned_language = language.strip().lower()
        normalized_voice = voice.strip() if isinstance(voice, str) and voice.strip() else None
        validate_engine_voice(
            engine=cleaned_engine,
            language=cleaned_language,
            voice=normalized_voice,
        )

        raw_bytes = _read_bounded_upload_bytes(file, max_bytes=MAX_UPLOAD_FILE_BYTES)
        extracted_text = extract_text_from_document(
            filename=file.filename,
            content_type=file.content_type,
            raw_bytes=raw_bytes,
            max_file_bytes=MAX_UPLOAD_FILE_BYTES,
        )
        validate_article_length(extracted_text)

        temp_dir, lock_file = create_locked_temp_dir()
        cleanup_once = make_once_cleanup(temp_dir, lock_file)
        temp_mp3_path = temp_dir / "article.mp3"

        saved_path = _invoke_tts_engine(
            text=extracted_text,
            output_path=str(temp_mp3_path),
            language=cleaned_language,
            engine=cleaned_engine,
            voice=normalized_voice,
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
    except ElevenLabsError as exc:
        logger.warning(
            "ElevenLabs synthesis error for document upload (code=%s, status=%d)",
            exc.error_code,
            exc.status_code,
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.user_message,
        ) from exc
    except (DocumentTooLargeError, ArticleTooLargeError) as exc:
        logger.warning("Document upload size/length limit exceeded (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except (DocumentValidationError, EmptyDocumentError, ValueError, TypeError) as exc:
        logger.info("Document validation error (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ConnectionError as exc:
        logger.warning("Upstream TTS connection error for document upload (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the speech synthesis service. Please try again.",
        ) from exc
    except (RuntimeError, OSError) as exc:
        logger.error("Internal processing error for document upload (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while generating the audio file.",
        ) from exc
    except Exception as exc:
        logger.error("Unexpected internal error for document upload (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while generating the audio file.",
        ) from exc
    finally:
        with suppress(Exception):
            file.file.close()
        if not ownership_transferred:
            if cleanup_once is not None:
                cleanup_once()
            elif temp_dir is not None or lock_file is not None:
                cleanup_temp_dir(temp_dir, lock_file)
