"""Module responsible for downloading and extracting content from web articles."""

from contextlib import closing
from functools import lru_cache
import ipaddress
import socket
from typing import Optional
from urllib.parse import urljoin, urlparse

import nltk
from newspaper import Article
from newspaper.article import ArticleException
from newspaper.configuration import Configuration
from newspaper.network import _get_html_from_response
import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError
from urllib3.util.connection import _set_socket_options

MAX_REDIRECTS: int = 5
REQUEST_TIMEOUT: int = 7
MAX_HTML_BYTES: int = 5 * 1024 * 1024  # 5 MB maximum raw HTML download size
DOWNLOAD_CHUNK_SIZE: int = 64 * 1024  # 64 KB streaming read chunk
ALLOWED_PORTS: frozenset[int] = frozenset({80, 443})
DEFAULT_SCHEME_PORTS: dict[str, int] = {
    "http": 80,
    "https": 443,
}
REQUIRED_NLTK_RESOURCES: tuple[str, ...] = ("punkt", "punkt_tab")

_BLOCKED_HOSTNAMES: frozenset[str] = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata.google.internal",
        "metadata",
    }
)
_BLOCKED_HOST_SUFFIXES: tuple[str, ...] = (
    ".localhost",
    ".local",
    ".internal",
    ".home.arpa",
)


class SSRFError(ValueError):
    """Raised when a URL targets a non-public, local, or restricted network address."""


class HTMLTooLargeError(ValueError):
    """Raised when the downloaded raw HTML response exceeds MAX_HTML_BYTES."""


def is_public_ip(ip_str: str) -> bool:
    """Return True only if the IPv4 or IPv6 address is globally routable and non-private."""
    try:
        ip_obj = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped

    return bool(
        ip_obj.is_global
        and not ip_obj.is_private
        and not ip_obj.is_loopback
        and not ip_obj.is_link_local
        and not ip_obj.is_multicast
        and not ip_obj.is_reserved
        and not ip_obj.is_unspecified
    )


def _validate_hostname_not_blocked(hostname: str) -> str:
    """Ensure the hostname is present and not a local domain name or non-public IP literal."""
    normalized = hostname.strip().lower().rstrip(".")
    if not normalized:
        raise SSRFError("URL must contain a valid hostname.")

    if normalized in _BLOCKED_HOSTNAMES or normalized.endswith(_BLOCKED_HOST_SUFFIXES):
        raise SSRFError(
            f"Access to local or internal hostname '{normalized}' is forbidden."
        )

    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        return normalized

    if not is_public_ip(normalized):
        raise SSRFError(
            f"Access to non-public IP address '{normalized}' is forbidden."
        )

    return normalized


def _resolve_and_validate_ips(hostname: str, port: int) -> list[tuple]:
    """Resolve DNS once and verify that all returned IPv4/IPv6 addresses are public.

    Args:
        hostname: Target hostname to resolve.
        port: Target TCP port (must be in ALLOWED_PORTS).

    Returns:
        The list of socket address info tuples from socket.getaddrinfo().

    Raises:
        ValueError: If port is not in ALLOWED_PORTS.
        SSRFError: If the hostname is blocked or any resolved IP is non-public.
        ConnectionError: If DNS resolution fails.
    """
    if port not in ALLOWED_PORTS:
        raise ValueError(
            f"Port {port} is not allowed. Only standard HTTP/HTTPS ports (80, 443) are permitted."
        )

    normalized_host = _validate_hostname_not_blocked(hostname)

    try:
        addr_info = socket.getaddrinfo(
            normalized_host,
            port,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ConnectionError(
            f"Could not resolve hostname '{normalized_host}': {exc}"
        ) from exc

    if not addr_info:
        raise ConnectionError(f"No IP addresses found for hostname '{normalized_host}'.")

    for item in addr_info:
        raw_ip = str(item[4][0]).split("%")[0]
        if not is_public_ip(raw_ip):
            raise SSRFError(
                f"Access to non-public IP address '{raw_ip}' for host '{normalized_host}' is forbidden."
            )

    return addr_info


def _establish_validated_socket(conn: HTTPConnection) -> socket.socket:
    """Resolve DNS once, verify all returned IPs are public, and connect a TCP socket."""
    host = conn._dns_host
    if host.startswith("["):
        host = host.strip("[]")

    addr_info = _resolve_and_validate_ips(host, conn.port)

    last_err: Optional[OSError] = None
    for af, socktype, proto, _canonname, sa in addr_info:
        sock: Optional[socket.socket] = None
        try:
            sock = socket.socket(af, socktype, proto)
            _set_socket_options(sock, conn.socket_options)
            if conn.timeout is not None and conn.timeout is not socket.getdefaulttimeout():
                sock.settimeout(conn.timeout)
            if conn.source_address:
                sock.bind(conn.source_address)
            sock.connect(sa)
            return sock
        except socket.timeout as exc:
            if sock is not None:
                sock.close()
            raise ConnectTimeoutError(
                conn,
                f"Connection to {conn.host} timed out. (connect timeout={conn.timeout})",
            ) from exc
        except OSError as exc:
            if sock is not None:
                sock.close()
            last_err = exc

    raise NewConnectionError(
        conn, f"Failed to establish a new connection: {last_err}"
    )


class _SafeHTTPConnection(HTTPConnection):
    """HTTPConnection that resolves DNS once, validates all IPs, and connects directly on port 80."""

    def _new_conn(self) -> socket.socket:
        if self.port != DEFAULT_SCHEME_PORTS["http"]:
            raise ValueError(
                f"Unsupported port '{self.port}' for scheme 'http'. Only default port 80 is allowed."
            )
        return _establish_validated_socket(self)


class _SafeHTTPSConnection(HTTPSConnection):
    """HTTPSConnection with single-inheritance from urllib3 HTTPSConnection.

    Preserves HTTPSConnection.__init__ and HTTPSConnection.connect (including
    TLS SNI, certificate chain verification, and certificate hostname matching
    against self.host) while routing TCP socket creation through _new_conn() on port 443.
    """

    def _new_conn(self) -> socket.socket:
        if self.port != DEFAULT_SCHEME_PORTS["https"]:
            raise ValueError(
                f"Unsupported port '{self.port}' for scheme 'https'. Only default port 443 is allowed."
            )
        return _establish_validated_socket(self)


class _SafeHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _SafeHTTPConnection


class _SafeHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _SafeHTTPSConnection


class SafeHTTPAdapter(HTTPAdapter):
    """Requests HTTPAdapter enforcing single-resolution SSRF validation at TCP socket creation."""

    def init_poolmanager(
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs,
    ) -> None:
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            "http": _SafeHTTPConnectionPool,
            "https": _SafeHTTPSConnectionPool,
        }


def validate_url(url: str) -> str:
    """Validate that the provided string is a safe, well-formed public HTTP/HTTPS URL on port 80/443.

    Args:
        url: The URL string to validate.

    Returns:
        The stripped, validated URL string.

    Raises:
        TypeError: If the URL is not a string.
        SSRFError: If the URL contains embedded credentials, targets a local/internal
            hostname, or uses a non-public IP literal.
        ValueError: If the URL is empty, contains whitespace, lacks a valid scheme/domain,
            or specifies a port other than 80 or 443.
    """
    if not isinstance(url, str):
        raise TypeError("The URL must be a string.")

    cleaned_url = url.strip()
    if not cleaned_url:
        raise ValueError("The URL cannot be empty.")

    if any(char.isspace() for char in cleaned_url):
        raise ValueError(
            f"Invalid URL '{cleaned_url}'. URLs cannot contain whitespace."
        )

    try:
        parsed = urlparse(cleaned_url)
    except ValueError as exc:
        raise ValueError(
            f"Invalid URL '{cleaned_url}'. Must include 'http://' or 'https://' and a valid domain."
        ) from exc

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(
            f"Invalid URL '{cleaned_url}'. Must include 'http://' or 'https://' and a valid domain."
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid port in URL '{cleaned_url}'.") from exc

    expected_port = DEFAULT_SCHEME_PORTS[parsed.scheme]
    if port is not None and port != expected_port:
        raise ValueError(
            f"Unsupported port '{port}' for scheme '{parsed.scheme}' in URL '{cleaned_url}'. "
            f"Only default port {expected_port} is allowed for '{parsed.scheme}'."
        )

    if parsed.username is not None or parsed.password is not None:
        raise SSRFError("URLs containing embedded credentials are not allowed.")

    _validate_hostname_not_blocked(parsed.hostname)
    return cleaned_url


def _read_bounded_html(
    response: requests.Response,
    max_bytes: int = MAX_HTML_BYTES,
) -> str:
    """Read response body in chunks enforcing max_bytes before decoding HTML."""
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared_bytes = int(content_length.strip())
        except ValueError:
            declared_bytes = -1
        if declared_bytes > max_bytes:
            raise HTMLTooLargeError(
                f"Article HTML response ({declared_bytes} bytes declared) exceeds the "
                f"maximum allowed download size of {max_bytes} bytes."
            )

    buffer = bytearray()
    for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE, decode_unicode=False):
        if not chunk:
            continue
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise HTMLTooLargeError(
                f"Article HTML download exceeded the maximum allowed size of {max_bytes} bytes."
            )

    response._content = bytes(buffer)
    response._content_consumed = True
    return _get_html_from_response(response)


def _extract_wrapped_ssrf_error(exc: BaseException) -> Optional[SSRFError]:
    """Traverse urllib3/requests exception wrappers to recover any nested SSRFError."""
    queue: list[BaseException] = [exc]
    visited: set[int] = set()

    while queue and len(visited) < 32:
        current = queue.pop(0)
        obj_id = id(current)
        if obj_id in visited:
            continue
        visited.add(obj_id)

        if isinstance(current, SSRFError):
            return current

        candidates = (
            current.__cause__,
            current.__context__,
            getattr(current, "reason", None),
            *getattr(current, "args", ()),
        )
        for item in candidates:
            if isinstance(item, BaseException) and id(item) not in visited:
                queue.append(item)

    return None


def download_article_html(
    url: str,
    config: Optional[Configuration] = None,
    max_html_bytes: int = MAX_HTML_BYTES,
) -> tuple[str, str]:
    """Safely download article HTML validating every redirect hop, pinning IPs, and bounding size.

    Args:
        url: Initial article URL to download.
        config: Optional newspaper3k Configuration instance.
        max_html_bytes: Maximum allowed raw HTML size in bytes (default: 5 MB).

    Returns:
        A tuple of (final_validated_url, html_content).

    Raises:
        TypeError: If the URL is not a string.
        HTMLTooLargeError: If the downloaded HTML exceeds max_html_bytes.
        SSRFError / ValueError: If the URL or any redirect target is invalid or non-public.
        ConnectionError: If a network, DNS, timeout, TLS, or HTTP status error occurs.
    """
    current_url = validate_url(url)
    cfg = config or Configuration()
    headers = cfg.headers or {"User-Agent": cfg.browser_user_agent}
    timeout = cfg.request_timeout or REQUEST_TIMEOUT

    with requests.Session() as session:
        session.trust_env = False
        adapter = SafeHTTPAdapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        for _ in range(MAX_REDIRECTS + 1):
            try:
                with closing(
                    session.get(
                        current_url,
                        headers=headers,
                        allow_redirects=False,
                        timeout=timeout,
                        stream=True,
                    )
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location or not location.strip():
                            raise ConnectionError(
                                f"Redirect response ({response.status_code}) from "
                                f"'{current_url}' is missing a Location header."
                            )
                        next_url = urljoin(current_url, location.strip())
                        current_url = validate_url(next_url)
                        continue

                    response.raise_for_status()
                    html = _read_bounded_html(response, max_bytes=max_html_bytes)
                    return current_url, html
            except (SSRFError, HTMLTooLargeError, ValueError):
                raise
            except RequestException as exc:
                ssrf_err = _extract_wrapped_ssrf_error(exc)
                if ssrf_err is not None:
                    raise ssrf_err from exc
                raise ConnectionError(
                    f"Failed to download the article from '{current_url}': {exc}"
                ) from exc

    raise SSRFError(
        f"Maximum redirect limit ({MAX_REDIRECTS}) exceeded for URL '{url}'."
    )


def prepare_nltk_resources() -> None:
    """Download required NLTK tokenizers during installation or application startup.

    Raises:
        RuntimeError: If a required NLTK resource is missing and cannot be downloaded.
    """
    for resource in REQUIRED_NLTK_RESOURCES:
        try:
            nltk.data.find(f"tokenizers/{resource}")
        except LookupError as exc:
            if not nltk.download(resource, quiet=True):
                raise RuntimeError(
                    f"Failed to download required NLTK resource '{resource}'."
                ) from exc


@lru_cache(maxsize=1)
def ensure_nltk_resources() -> None:
    """Verify that required NLTK tokenizers are already installed locally.

    Does not perform network downloads during user requests. If a resource is
    missing, raises RuntimeError instructing to run prepare_nltk_resources()
    or install the NLTK data beforehand.

    Raises:
        RuntimeError: If a required NLTK resource is not installed locally.
    """
    for resource in REQUIRED_NLTK_RESOURCES:
        try:
            nltk.data.find(f"tokenizers/{resource}")
        except LookupError as exc:
            raise RuntimeError(
                f"Required NLTK resource '{resource}' is not installed. "
                f"Run 'python -m nltk.downloader punkt punkt_tab' during setup."
            ) from exc


def format_article_content(title: Optional[str], body: str) -> str:
    """Clean and structure the extracted title and body into a single readable text.

    Args:
        title: The extracted article title (may be None or empty).
        body: The extracted main text of the article.

    Returns:
        A clean, structured string combining the title and paragraphs.
    """
    clean_title = " ".join(title.split()) if title else ""
    paragraphs = [
        " ".join(line.split())
        for line in (body or "").splitlines()
        if line.strip()
    ]

    while clean_title and paragraphs and paragraphs[0].lower() == clean_title.lower():
        paragraphs.pop(0)

    clean_body = "\n\n".join(paragraphs)

    if clean_title and clean_body:
        return f"{clean_title}\n\n{clean_body}"

    return clean_body or clean_title


def extract_article(url: str) -> str:
    """Extract the title and main text content from a web article URL.

    Downloads the HTML once through a safe SSRF-protected transport layer with
    a 5 MB streaming size cap and hands it to newspaper3k via input_html with
    meta-refresh and image fetching disabled.

    Args:
        url: The web address of the article to extract.

    Returns:
        The extracted and formatted text content (title and main body).

    Raises:
        TypeError: If the URL argument is not a string.
        HTMLTooLargeError: If the raw HTML response exceeds 5 MB.
        SSRFError / ValueError: If the URL is invalid, uses a disallowed port,
            targets a non-public address, exceeds the redirect limit, or has no text.
        ConnectionError: If downloading the article fails due to network, DNS, TLS, or HTTP errors.
        RuntimeError: If parsing, NLP processing, or local NLTK resource lookup fails.
    """
    validated_url = validate_url(url)
    ensure_nltk_resources()

    config = Configuration()
    config.follow_meta_refresh = False
    config.fetch_images = False

    final_url, html = download_article_html(validated_url, config=config)

    article = Article(final_url, config=config)

    try:
        article.download(input_html=html)
        article.parse()
    except (ArticleException, ValueError, LookupError) as exc:
        raise RuntimeError(
            f"Failed to parse the article HTML at '{final_url}': {exc}"
        ) from exc

    if not article.text or not article.text.strip():
        raise ValueError(
            f"No article text content could be extracted from '{final_url}'."
        )

    try:
        article.nlp()
    except (ArticleException, LookupError) as exc:
        raise RuntimeError(
            f"Failed to process natural language for the article at '{final_url}': {exc}"
        ) from exc

    return format_article_content(article.title, article.text)
