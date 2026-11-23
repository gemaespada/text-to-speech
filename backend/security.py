"""Security utilities: SSRF exports, text length limits, proxy IP validation, and rate limiting."""

from collections import deque
from functools import lru_cache
import ipaddress
import logging
import os
import threading
import time
from typing import Mapping, Optional

from src.extractor import HTMLTooLargeError, MAX_HTML_BYTES, SSRFError, is_public_ip

logger = logging.getLogger("tts_backend.security")


def _load_positive_int_env(var_name: str, default: int) -> int:
    """Parse an environment variable as a strictly positive integer (> 0)."""
    raw = os.getenv(var_name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"Configuration error: {var_name} must be an integer, got '{raw}'."
        ) from exc
    if value <= 0:
        raise ValueError(
            f"Configuration error: {var_name} must be a positive integer (> 0), got {value}."
        )
    return value


MAX_ARTICLE_CHARS: int = _load_positive_int_env("MAX_ARTICLE_CHARS", 15000)
RATE_LIMIT_REQUESTS: int = _load_positive_int_env("RATE_LIMIT_REQUESTS", 5)
RATE_LIMIT_WINDOW_SECONDS: int = _load_positive_int_env("RATE_LIMIT_WINDOW_SECONDS", 60)
MAX_RATE_LIMIT_IPS: int = _load_positive_int_env("MAX_RATE_LIMIT_IPS", 10000)

# Empty by default: never trust private networks or proxy headers unless
# explicitly configured and verified in the deployment environment.
DEFAULT_TRUSTED_PROXY_CIDRS: str = ""


class ArticleTooLargeError(ValueError):
    """Raised when the extracted article text exceeds the maximum allowed character limit."""


class RateLimitExceededError(Exception):
    """Raised when a client IP exceeds the allowed number of requests per time window."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, retry_after_seconds)
        super().__init__(
            f"Rate limit exceeded. Maximum {RATE_LIMIT_REQUESTS} conversions per "
            f"{RATE_LIMIT_WINDOW_SECONDS} seconds. Retry in {self.retry_after_seconds}s."
        )


def validate_article_length(text: str, max_chars: int = MAX_ARTICLE_CHARS) -> str:
    """Ensure the extracted article text does not exceed the configured character limit."""
    if max_chars <= 0:
        raise ValueError("max_chars must be a positive integer.")
    if len(text) > max_chars:
        raise ArticleTooLargeError(
            f"The extracted article text ({len(text)} characters) exceeds the "
            f"maximum allowed limit of {max_chars} characters."
        )
    return text


def normalize_ip(ip_raw: str) -> Optional[str]:
    """Validate and normalize an IPv4 or IPv6 address string to canonical form.

    Unwraps IPv4-mapped IPv6 addresses (e.g., '::ffff:203.0.113.5' -> '203.0.113.5').
    Returns None if ip_raw is not a valid IP address.
    """
    candidate = ip_raw.strip()
    if not candidate:
        return None
    try:
        ip_obj = ipaddress.ip_address(candidate)
    except ValueError:
        return None

    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped

    return str(ip_obj)


@lru_cache(maxsize=8)
def _parse_trusted_networks(
    cidrs_raw: str,
) -> tuple[ipaddress._BaseNetwork, ...]:
    """Parse a comma-separated list of trusted proxy CIDRs, failing on any invalid entry.

    Raises:
        ValueError: If any CIDR token in cidrs_raw is invalid.
    """
    networks: list[ipaddress._BaseNetwork] = []
    for item in cidrs_raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError as exc:
            raise ValueError(
                f"Invalid CIDR '{candidate}' in TRUSTED_PROXY_CIDRS configuration."
            ) from exc
    return tuple(networks)


def validate_security_config() -> None:
    """Validate all security environment settings at application startup.

    Raises:
        ValueError: If numeric limits are non-positive or TRUSTED_PROXY_CIDRS contains
            any malformed CIDR entry.
    """
    _load_positive_int_env("MAX_ARTICLE_CHARS", MAX_ARTICLE_CHARS)
    _load_positive_int_env("RATE_LIMIT_REQUESTS", RATE_LIMIT_REQUESTS)
    _load_positive_int_env("RATE_LIMIT_WINDOW_SECONDS", RATE_LIMIT_WINDOW_SECONDS)
    _load_positive_int_env("MAX_RATE_LIMIT_IPS", MAX_RATE_LIMIT_IPS)
    _load_positive_int_env("TRUSTED_PROXY_DEPTH", 1)

    cidrs = os.getenv("TRUSTED_PROXY_CIDRS", DEFAULT_TRUSTED_PROXY_CIDRS).strip()
    if cidrs:
        _parse_trusted_networks(cidrs)


def is_trusted_proxy_ip(peer_ip: str, trusted_cidrs: Optional[str] = None) -> bool:
    """Return True only if peer_ip belongs to a valid, explicitly configured trusted proxy CIDR.

    If TRUSTED_PROXY_CIDRS contains any invalid CIDR entry, fails closed (returns False)
    rather than activating a partially invalid configuration.
    """
    normalized = normalize_ip(peer_ip)
    if normalized is None:
        return False

    ip_obj = ipaddress.ip_address(normalized)
    cidrs = (
        trusted_cidrs
        if trusted_cidrs is not None
        else os.getenv("TRUSTED_PROXY_CIDRS", DEFAULT_TRUSTED_PROXY_CIDRS)
    )
    if not cidrs.strip():
        return False

    try:
        networks = _parse_trusted_networks(cidrs)
    except ValueError as exc:
        logger.error("Disabling proxy trust due to invalid TRUSTED_PROXY_CIDRS: %s", exc)
        return False

    for network in networks:
        if ip_obj.version == network.version and ip_obj in network:
            return True
    return False


def extract_client_ip(
    peer_host: Optional[str],
    headers: Mapping[str, str],
) -> str:
    """Safely resolve and normalize the client IP without allowing X-Forwarded-For spoofing.

    How client IP resolution works:
    1. By default, TRUST_PROXY_HEADERS='false' and TRUSTED_PROXY_CIDRS=''. All proxy
       headers are ignored and the normalized socket peer IP (peer_host) is returned.
    2. Even when TRUST_PROXY_HEADERS='true', X-Forwarded-For is only inspected if
       peer_host belongs to a valid CIDR in TRUSTED_PROXY_CIDRS.
    3. TRUSTED_PROXY_DEPTH (default: 1) specifies how many trusted reverse proxies
       append to X-Forwarded-For. The client IP is selected at index
       `len(hops) - TRUSTED_PROXY_DEPTH` (counting from the right). If X-Forwarded-For
       contains fewer hops than TRUSTED_PROXY_DEPTH, the header is rejected without
       falling back to hops[0].
    4. The selected X-Forwarded-For address must be a valid, globally routable PUBLIC
       IP address (`is_public_ip(candidate_ip) == True`). Private, loopback, reserved,
       or link-local addresses inside X-Forwarded-For are never accepted as the real
       client IP; if found, the function falls back safely to direct_ip.
    """
    raw_peer = (peer_host or "").strip()
    direct_ip = normalize_ip(raw_peer) or (raw_peer or "unknown")

    trust_proxy = os.getenv("TRUST_PROXY_HEADERS", "false").strip().lower() == "true"
    if not trust_proxy or not is_trusted_proxy_ip(direct_ip):
        return direct_ip

    try:
        proxy_depth = int(os.getenv("TRUSTED_PROXY_DEPTH", "1"))
    except ValueError:
        return direct_ip

    if proxy_depth < 1:
        return direct_ip

    forwarded_for = headers.get("x-forwarded-for", "")
    if not forwarded_for:
        return direct_ip

    hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
    if len(hops) < proxy_depth:
        return direct_ip

    candidate_ip = normalize_ip(hops[len(hops) - proxy_depth])
    if candidate_ip is None or not is_public_ip(candidate_ip):
        return direct_ip

    return candidate_ip


class InMemoryRateLimiter:
    """Thread-safe, per-process sliding-window rate limiter with bounded IP table size.

    Note:
        This rate limiter stores request timestamps in process memory. It enforces
        limits locally per Python worker process and is not distributed across
        multiple worker processes or multiple container instances.
    """

    def __init__(
        self,
        max_requests: int = RATE_LIMIT_REQUESTS,
        window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
        max_tracked_ips: int = MAX_RATE_LIMIT_IPS,
    ) -> None:
        if max_requests <= 0 or window_seconds <= 0 or max_tracked_ips <= 0:
            raise ValueError(
                "max_requests, window_seconds, and max_tracked_ips must all be > 0."
            )
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_tracked_ips = max_tracked_ips
        self._hits: dict[str, deque[float]] = {}
        self._last_cleanup: float = time.monotonic()
        self._lock = threading.Lock()

    def _prune_expired_unlocked(self, cutoff: float, now: float) -> None:
        """Remove all IP keys whose timestamps have completely expired."""
        expired_ips = [
            ip
            for ip, timestamps in self._hits.items()
            if not timestamps or timestamps[-1] <= cutoff
        ]
        for ip in expired_ips:
            del self._hits[ip]
        self._last_cleanup = now

    def check(self, client_ip: str) -> None:
        """Record a request for client_ip or raise RateLimitExceededError if over quota."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            if (now - self._last_cleanup) >= self.window_seconds:
                self._prune_expired_unlocked(cutoff, now)

            timestamps = self._hits.get(client_ip)
            if timestamps is not None:
                while timestamps and timestamps[0] <= cutoff:
                    timestamps.popleft()
                if not timestamps:
                    del self._hits[client_ip]
                    timestamps = None

            if timestamps is None:
                if len(self._hits) >= self.max_tracked_ips:
                    self._prune_expired_unlocked(cutoff, now)
                    if len(self._hits) >= self.max_tracked_ips:
                        oldest_ts = min(
                            (dq[0] for dq in self._hits.values() if dq),
                            default=now,
                        )
                        retry_after = max(1, int(self.window_seconds - (now - oldest_ts)) + 1)
                        raise RateLimitExceededError(retry_after_seconds=retry_after)

                timestamps = deque()
                self._hits[client_ip] = timestamps

            if len(timestamps) >= self.max_requests:
                retry_after = int(self.window_seconds - (now - timestamps[0])) + 1
                raise RateLimitExceededError(retry_after_seconds=retry_after)

            timestamps.append(now)

    def active_ip_count(self) -> int:
        """Return the number of currently tracked IP entries in memory."""
        with self._lock:
            return len(self._hits)

    def reset(self) -> None:
        """Clear all tracked client request timestamps."""
        with self._lock:
            self._hits.clear()
            self._last_cleanup = time.monotonic()


__all__ = [
    "ArticleTooLargeError",
    "DEFAULT_TRUSTED_PROXY_CIDRS",
    "HTMLTooLargeError",
    "InMemoryRateLimiter",
    "MAX_ARTICLE_CHARS",
    "MAX_HTML_BYTES",
    "MAX_RATE_LIMIT_IPS",
    "RATE_LIMIT_REQUESTS",
    "RATE_LIMIT_WINDOW_SECONDS",
    "RateLimitExceededError",
    "SSRFError",
    "extract_client_ip",
    "is_public_ip",
    "is_trusted_proxy_ip",
    "normalize_ip",
    "validate_article_length",
    "validate_security_config",
]
