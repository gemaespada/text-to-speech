"""Bounded, thread-safe disk cache for URL-based TTS MP3 conversions."""

from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("tts_backend.cache")

_HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

DEFAULT_CACHE_TTL_SECONDS: float = float(
    os.getenv("TTS_CACHE_TTL_SECONDS", "3600")
)
DEFAULT_CACHE_MAX_ENTRIES: int = int(
    os.getenv("TTS_CACHE_MAX_ENTRIES", "50")
)
DEFAULT_CACHE_MAX_BYTES: int = int(
    os.getenv("TTS_CACHE_MAX_BYTES", str(100 * 1024 * 1024))
)


@dataclass
class _CacheEntry:
    """In-memory metadata record for a cached MP3 file."""

    key: str
    file_path: Path
    size_bytes: int
    created_at: float
    last_accessed: float


def build_url_cache_key(
    url: str,
    engine: str,
    language: str,
    voice: str,
) -> str:
    """Generate a deterministic 64-character SHA-256 hex cache key for (url, engine, language, voice)."""
    canonical_payload = json.dumps(
        {
            "url": url.strip(),
            "engine": engine.strip().lower(),
            "language": language.strip().lower(),
            "voice": voice.strip(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


class ConversionCache:
    """Thread-safe LRU + TTL disk cache for URL audio conversions."""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        max_entries: int = DEFAULT_CACHE_MAX_ENTRIES,
        max_total_bytes: int = DEFAULT_CACHE_MAX_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0.")
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0.")
        if max_total_bytes <= 0:
            raise ValueError("max_total_bytes must be > 0.")

        default_dir = Path(tempfile.gettempdir()) / "tts_url_conversion_cache"
        self.cache_dir: Path = (cache_dir or default_dir).resolve()
        self.ttl_seconds: float = float(ttl_seconds)
        self.max_entries: int = int(max_entries)
        self.max_total_bytes: int = int(max_total_bytes)
        self._clock: Callable[[], float] = clock
        self._lock = threading.Lock()
        self._entries: "OrderedDict[str, _CacheEntry]" = OrderedDict()

    def _validate_cache_key(self, key: str) -> str:
        """Ensure key is strictly a 64-char hex SHA-256 string to prevent path traversal."""
        if not isinstance(key, str) or not _HEX_SHA256_RE.match(key):
            raise ValueError("Invalid cache key format; expected 64-char lowercase hex SHA-256.")
        return key

    def _safe_entry_path(self, key: str) -> Path:
        """Resolve and verify that the cache file path resides strictly inside self.cache_dir."""
        validated_key = self._validate_cache_key(key)
        candidate = (self.cache_dir / f"{validated_key}.mp3").resolve()
        if candidate.parent != self.cache_dir:
            raise ValueError("Resolved cache file path escapes cache directory.")
        return candidate

    def _ensure_cache_dir_unlocked(self) -> bool:
        """Create the cache directory if missing; return False if filesystem creation fails."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as exc:
            logger.warning("Could not create conversion cache directory: %s", type(exc).__name__)
            return False

    def _remove_entry_unlocked(self, key: str) -> None:
        """Remove a single entry from the index and delete its file from disk."""
        entry = self._entries.pop(key, None)
        target_path = entry.file_path if entry is not None else None
        if target_path is None:
            with suppress(ValueError):
                target_path = self._safe_entry_path(key)
        if target_path is not None:
            with suppress(OSError):
                target_path.unlink(missing_ok=True)

    def _evict_expired_unlocked(self, now: float) -> int:
        """Remove all entries whose age exceeds self.ttl_seconds."""
        expired_keys = [
            k
            for k, entry in self._entries.items()
            if (now - entry.created_at) >= self.ttl_seconds or not entry.file_path.is_file()
        ]
        for k in expired_keys:
            self._remove_entry_unlocked(k)
        return len(expired_keys)

    def _total_bytes_unlocked(self) -> int:
        return sum(entry.size_bytes for entry in self._entries.values())

    def _enforce_limits_unlocked(self, incoming_bytes: int = 0) -> None:
        """Evict least-recently-used entries until count and total byte limits are satisfied."""
        while self._entries and (
            len(self._entries) >= self.max_entries
            or (self._total_bytes_unlocked() + incoming_bytes) > self.max_total_bytes
        ):
            oldest_key, _ = next(iter(self._entries.items()))
            self._remove_entry_unlocked(oldest_key)

    def contains(self, key: str) -> bool:
        """Return True if key is currently present, unexpired, and backed by a file in cache."""
        try:
            validated_key = self._validate_cache_key(key)
        except ValueError:
            return False

        now = self._clock()
        with self._lock:
            self._evict_expired_unlocked(now)
            entry = self._entries.get(validated_key)
            if entry is None:
                return False
            if not entry.file_path.is_file():
                self._remove_entry_unlocked(validated_key)
                return False
            return True

    def copy_to_destination(self, key: str, destination_path: Path) -> bool:
        """If key is present and valid in cache, copy the MP3 to destination_path and return True.

        Returns False on cache miss, expired entry, or any disk read/write failure.
        """
        try:
            validated_key = self._validate_cache_key(key)
        except ValueError:
            return False

        now = self._clock()
        with self._lock:
            self._evict_expired_unlocked(now)
            entry = self._entries.get(validated_key)
            if entry is None:
                return False

            if not entry.file_path.is_file():
                self._remove_entry_unlocked(validated_key)
                return False

            try:
                if entry.file_path.stat().st_size <= 0:
                    self._remove_entry_unlocked(validated_key)
                    return False
                shutil.copyfile(entry.file_path, destination_path)
                if not destination_path.is_file() or destination_path.stat().st_size <= 0:
                    return False
            except OSError as exc:
                logger.warning("Cache read/copy failed for key %s: %s", validated_key[:12], type(exc).__name__)
                self._remove_entry_unlocked(validated_key)
                return False

            entry.last_accessed = now
            self._entries.move_to_end(validated_key)
            return True

    def put_from_file(self, key: str, source_path: Path) -> bool:
        """Store source_path MP3 into the cache under key. Returns True if cached, False otherwise."""
        try:
            validated_key = self._validate_cache_key(key)
            target_path = self._safe_entry_path(validated_key)
        except ValueError:
            return False

        try:
            if not source_path.is_file():
                return False
            file_size = source_path.stat().st_size
        except OSError:
            return False

        if file_size <= 0 or file_size > self.max_total_bytes:
            return False

        now = self._clock()
        with self._lock:
            if not self._ensure_cache_dir_unlocked():
                return False

            self._evict_expired_unlocked(now)
            if validated_key in self._entries:
                self._remove_entry_unlocked(validated_key)

            self._enforce_limits_unlocked(incoming_bytes=file_size)

            temp_cache_path: Optional[Path] = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=self.cache_dir,
                    prefix=f".cache_{validated_key[:12]}_",
                    suffix=".tmp",
                    delete=False,
                ) as tmp_file:
                    temp_cache_path = Path(tmp_file.name)

                shutil.copyfile(source_path, temp_cache_path)
                temp_cache_path.replace(target_path)

                self._entries[validated_key] = _CacheEntry(
                    key=validated_key,
                    file_path=target_path,
                    size_bytes=file_size,
                    created_at=now,
                    last_accessed=now,
                )
                return True
            except OSError as exc:
                logger.warning("Cache write failed for key %s: %s", validated_key[:12], type(exc).__name__)
                with suppress(OSError):
                    target_path.unlink(missing_ok=True)
                return False
            finally:
                if temp_cache_path is not None:
                    with suppress(OSError):
                        temp_cache_path.unlink(missing_ok=True)

    def evict_expired(self) -> int:
        """Manually trigger eviction of expired cache entries."""
        with self._lock:
            return self._evict_expired_unlocked(self._clock())

    def clear(self) -> None:
        """Remove all cached entries and delete their files from disk."""
        with self._lock:
            keys = list(self._entries.keys())
            for k in keys:
                self._remove_entry_unlocked(k)
            if self.cache_dir.is_dir():
                for leftover in self.cache_dir.glob("*.mp3"):
                    with suppress(OSError):
                        leftover.unlink(missing_ok=True)
                for leftover_tmp in self.cache_dir.glob(".cache_*.tmp"):
                    with suppress(OSError):
                        leftover_tmp.unlink(missing_ok=True)

    def stats(self) -> dict[str, int]:
        """Return current cache entry count and total byte usage."""
        with self._lock:
            self._evict_expired_unlocked(self._clock())
            return {
                "entries": len(self._entries),
                "total_bytes": self._total_bytes_unlocked(),
                "max_entries": self.max_entries,
                "max_total_bytes": self.max_total_bytes,
            }
