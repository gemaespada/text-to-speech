"""Comprehensive pytest suite for URL security, manual text, file uploads, dual engines, and caching."""

import asyncio
import io
import os
from pathlib import Path
import shutil
import tempfile
from unittest.mock import MagicMock, patch
import zipfile

import docx
from fastapi import HTTPException, Request, UploadFile
from pydantic import ValidationError
import pytest
from starlette.datastructures import Headers

from backend.cache import ConversionCache, build_url_cache_key
import backend.main as backend_main
from backend.schemas import TextTTSRequest, TTSRequest
import src.converter as converter
from src.document_extractor import (
    DocumentTooLargeError,
    DocumentValidationError,
    EmptyDocumentError,
    extract_text_from_document,
    sanitize_upload_filename,
)
from src.extractor import SSRFError, _resolve_and_validate_ips, download_article_html, validate_url


def _make_scope(path: str = "/api/tts", client_ip: str = "203.0.113.50") -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": (client_ip, 54321),
    }


def _make_request(path: str = "/api/tts", client_ip: str = "203.0.113.50") -> Request:
    return Request(_make_scope(path=path, client_ip=client_ip))


def _consume_asgi_response(response, path: str = "/api/tts") -> bytes:
    sent_messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent_messages.append(message)

    asyncio.run(response(_make_scope(path=path), receive, send))
    return b"".join(
        m.get("body", b"") for m in sent_messages if m["type"] == "http.response.body"
    )


def _build_valid_docx_bytes(paragraphs: list[str]) -> bytes:
    """Create a real in-memory DOCX file containing the given paragraphs."""
    doc = docx.Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _build_valid_epub_bytes(chapters: list[str]) -> bytes:
    """Create a real in-memory EPUB archive with container.xml, content.opf, and XHTML chapters."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles>
                <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
              </rootfiles>
            </container>""",
        )

        manifest_items = []
        spine_items = []
        for idx, chapter_text in enumerate(chapters, start=1):
            item_id = f"ch{idx}"
            href = f"chapter{idx}.xhtml"
            manifest_items.append(
                f'<item id="{item_id}" href="{href}" media-type="application/xhtml+xml"/>'
            )
            spine_items.append(f'<itemref idref="{item_id}"/>')
            xhtml = f"""<?xml version="1.0" encoding="UTF-8"?>
            <html xmlns="http://www.w3.org/1999/xhtml">
              <head><title>Capítulo {idx}</title><script>alert(1)</script></head>
              <body><p>{chapter_text}</p></body>
            </html>"""
            zf.writestr(f"OEBPS/{href}", xhtml)

        opf = f"""<?xml version="1.0" encoding="UTF-8"?>
        <package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="BookId">
          <manifest>{"".join(manifest_items)}</manifest>
          <spine>{"".join(spine_items)}</spine>
        </package>"""
        zf.writestr("OEBPS/content.opf", opf)
    return buf.getvalue()


def _build_minimal_pdf_bytes_with_text(text_content: str) -> bytes:
    """Construct a valid minimal PDF 1.4 document with an embedded Helvetica text stream."""
    escaped = (
        text_content.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )
    stream_data = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        f"4 0 obj\n<< /Length {len(stream_data)} >>\nstream\n".encode("latin-1")
        + stream_data
        + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("latin-1"))
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.extend(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "latin-1"
        )
    )
    return bytes(out)


def _build_blank_pdf_bytes() -> bytes:
    """Construct a valid 1-page PDF with zero text streams (simulating a scanned/blank PDF)."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_upload_file(
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


@pytest.fixture(autouse=True)
def reset_state_and_sandbox():
    """Reset rate limiter, conversion cache, and create an isolated temp directory per test."""
    backend_main.rate_limiter.reset()
    converter.clear_elevenlabs_voices_cache()
    sandbox = Path(tempfile.mkdtemp(prefix="tts_pytest_sandbox_"))
    yield sandbox
    backend_main.rate_limiter.reset()
    shutil.rmtree(sandbox, ignore_errors=True)


# =============================================================================
# 1. URL Security & SSRF Validation Tests
# =============================================================================


@pytest.mark.parametrize(
    "valid_url",
    [
        "http://example.com/news/story",
        "https://example.com/news/story",
        "http://example.com:80/path?query=1",
        "https://example.com:443/path?query=1",
    ],
)
def test_url_security_valid_http_and_https(valid_url: str):
    assert validate_url(valid_url) == valid_url
    req = TTSRequest(url=valid_url, language="es")
    assert req.url == valid_url


@pytest.mark.parametrize(
    "disallowed_url",
    [
        "ftp://example.com/file.txt",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "gopher://example.com:70/",
        "data:text/plain;base64,SGVsbG8=",
        "http://example.com:8080/article",
        "https://example.com:8443/article",
        "http://example.com:443/article",
        "https://example.com:80/article",
        "http://user:password@example.com/private",
        "https://example.com/has spaces/article",
        "",
        "   ",
        "not-a-url",
    ],
)
def test_url_security_disallowed_schemes_ports_and_malformed(disallowed_url: str):
    with pytest.raises((ValueError, SSRFError)):
        validate_url(disallowed_url)
    with pytest.raises(ValidationError):
        TTSRequest(url=disallowed_url, language="es")


@pytest.mark.parametrize(
    "restricted_url",
    [
        "http://localhost/admin",
        "http://localhost.localdomain/test",
        "http://127.0.0.1/secret",
        "http://10.0.0.1/internal",
        "http://172.16.0.5/internal",
        "http://192.168.1.100/router",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/loopback",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://printer.local/status",
        "http://db.internal/dump",
    ],
)
def test_url_security_blocks_private_loopback_and_reserved_addresses(restricted_url: str):
    with pytest.raises(SSRFError):
        validate_url(restricted_url)
    with pytest.raises(ValidationError):
        TTSRequest(url=restricted_url, language="es")


def test_url_security_blocks_dns_rebinding_to_private_ip():
    fake_addr_info = [(2, 1, 6, "", ("10.20.30.40", 80))]
    with patch("src.extractor.socket.getaddrinfo", return_value=fake_addr_info):
        with pytest.raises(SSRFError, match="non-public IP"):
            _resolve_and_validate_ips("evil-rebind.example.com", 80)


def test_url_security_blocks_redirect_to_disallowed_destination():
    redirect_resp = MagicMock()
    redirect_resp.status_code = 302
    redirect_resp.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
    redirect_resp.__enter__.return_value = redirect_resp
    redirect_resp.__exit__.return_value = False

    with patch("src.extractor.requests.Session.get", return_value=redirect_resp):
        with pytest.raises(SSRFError):
            download_article_html("https://example.com/public-start")


# =============================================================================
# 2. Manual Text Input Mode Tests (POST /api/tts/text)
# =============================================================================


def test_manual_text_valid_conversion_and_temp_cleanup(reset_state_and_sandbox: Path):
    orig_create = backend_main.create_locked_temp_dir

    def sandboxed_create():
        return orig_create(temp_root=reset_state_and_sandbox)

    def fake_tts(text: str, output_path: str, language: str = "es") -> str:
        Path(output_path).write_bytes(f"MP3:{language}:{text}".encode("utf-8"))
        return output_path

    with patch("backend.main.create_locked_temp_dir", side_effect=sandboxed_create), \
         patch("backend.main.text_to_speech", side_effect=fake_tts):
        payload = TextTTSRequest(
            text="Este es un texto manual válido para convertir a voz.",
            engine="edge",
            language="es",
        )
        response = backend_main.convert_text_to_mp3(payload, _make_request("/api/tts/text"))
        body = _consume_asgi_response(response, path="/api/tts/text")

    assert body == b"MP3:es:Este es un texto manual v\xc3\xa1lido para convertir a voz."
    assert list(reset_state_and_sandbox.glob(f"{backend_main.TEMP_DIR_PREFIX}*")) == []
    # Manual text must NEVER be stored in the URL conversion cache
    assert backend_main.conversion_cache.stats()["entries"] == 0


@pytest.mark.parametrize("bad_text", ["", "   ", "\n\t  ", "Texto con \x00 nulo"])
def test_manual_text_rejects_empty_or_null_bytes(bad_text: str):
    with pytest.raises(ValidationError):
        TextTTSRequest(text=bad_text, engine="edge", language="es")


def test_manual_text_rejects_exceeding_15000_chars(reset_state_and_sandbox: Path):
    payload = TextTTSRequest(text="A" * 15001, engine="edge", language="es")
    with pytest.raises(HTTPException) as exc_info:
        backend_main.convert_text_to_mp3(payload, _make_request("/api/tts/text"))
    assert exc_info.value.status_code == 413
    assert list(reset_state_and_sandbox.glob(f"{backend_main.TEMP_DIR_PREFIX}*")) == []


# =============================================================================
# 3. Document File Upload Tests (PDF, DOCX, TXT, EPUB & Security Checks)
# =============================================================================


def test_document_extractor_valid_txt_docx_epub_and_pdf():
    # 1. TXT
    txt_bytes = "Título del documento TXT\n\nSegundo párrafo de prueba.".encode("utf-8")
    extracted_txt = extract_text_from_document("nota.txt", "text/plain", txt_bytes)
    assert "Título del documento TXT" in extracted_txt
    assert "Segundo párrafo de prueba." in extracted_txt

    # 2. DOCX
    docx_bytes = _build_valid_docx_bytes(["Encabezado DOCX", "Contenido del documento Word."])
    extracted_docx = extract_text_from_document(
        "informe.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        docx_bytes,
    )
    assert "Encabezado DOCX" in extracted_docx
    assert "Contenido del documento Word." in extracted_docx

    # 3. EPUB
    epub_bytes = _build_valid_epub_bytes(["Capítulo uno del libro.", "Capítulo dos del libro."])
    extracted_epub = extract_text_from_document(
        "libro.epub",
        "application/epub+zip",
        epub_bytes,
    )
    assert "Capítulo uno del libro." in extracted_epub
    assert "Capítulo dos del libro." in extracted_epub
    assert "alert(1)" not in extracted_epub

    # 4. PDF
    pdf_bytes = _build_minimal_pdf_bytes_with_text("Hello PDF Text Extraction")
    extracted_pdf = extract_text_from_document(
        "documento.pdf",
        "application/pdf",
        pdf_bytes,
    )
    assert "Hello PDF Text Extraction" in extracted_pdf


def test_document_extractor_rejects_empty_corrupted_fake_extension_and_wrong_mime():
    # Empty 0-byte file
    with pytest.raises(EmptyDocumentError):
        extract_text_from_document("vacio.txt", "text/plain", b"")

    # Unsupported extension
    with pytest.raises(DocumentValidationError):
        extract_text_from_document("script.exe", "application/octet-stream", b"MZ-binary")

    # Wrong MIME for extension
    with pytest.raises(DocumentValidationError, match="MIME"):
        extract_text_from_document("archivo.pdf", "image/png", _build_minimal_pdf_bytes_with_text("Hi"))

    # Fake extension: plain text disguised as .pdf
    with pytest.raises(DocumentValidationError, match="PDF"):
        extract_text_from_document("falso.pdf", "application/pdf", b"Esto no es un PDF real")

    # Fake extension: PDF binary disguised as .txt
    with pytest.raises(DocumentValidationError, match="binario"):
        extract_text_from_document("falso.txt", "text/plain", b"%PDF-1.4 contenido binario")

    # Fake extension: EPUB archive disguised as .docx
    epub_bytes = _build_valid_epub_bytes(["Texto EPUB"])
    with pytest.raises(DocumentValidationError, match="Word"):
        extract_text_from_document("falso.docx", "application/zip", epub_bytes)

    # Corrupted PDF
    with pytest.raises(DocumentValidationError):
        extract_text_from_document("corrupto.pdf", "application/pdf", b"%PDF-1.4\n%%corrupted_stream")

    # Corrupted DOCX
    with pytest.raises(DocumentValidationError):
        extract_text_from_document("corrupto.docx", "application/zip", b"PK\x03\x04not_a_valid_zip")

    # Blank / scanned PDF with no extractable text
    blank_pdf = _build_blank_pdf_bytes()
    with pytest.raises(EmptyDocumentError, match="PDF"):
        extract_text_from_document("escaneado.pdf", "application/pdf", blank_pdf)

    # Oversized file
    with pytest.raises(DocumentTooLargeError):
        extract_text_from_document(
            "grande.txt",
            "text/plain",
            b"A" * 200,
            max_file_bytes=100,
        )

    # Path traversal in filename is sanitized to basename
    safe_name, ext = sanitize_upload_filename("../../etc/secret/documento.txt")
    assert safe_name == "documento.txt"
    assert ext == ".txt"


def test_file_upload_endpoint_success_and_error_temp_cleanup(reset_state_and_sandbox: Path):
    orig_create = backend_main.create_locked_temp_dir

    def sandboxed_create():
        return orig_create(temp_root=reset_state_and_sandbox)

    def fake_tts(text: str, output_path: str, language: str = "es") -> str:
        Path(output_path).write_bytes(f"FILE-MP3:{text}".encode("utf-8"))
        return output_path

    # 1. Valid DOCX upload through POST /api/tts/file
    docx_bytes = _build_valid_docx_bytes(["Texto desde archivo DOCX."])
    upload = _make_upload_file(
        "prueba.docx",
        docx_bytes,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    with patch("backend.main.create_locked_temp_dir", side_effect=sandboxed_create), \
         patch("backend.main.text_to_speech", side_effect=fake_tts):
        response = backend_main.convert_file_to_mp3(
            request=_make_request("/api/tts/file"),
            file=upload,
            language="es",
            engine="edge",
            voice=None,
        )
        body = _consume_asgi_response(response, path="/api/tts/file")

    assert body == b"FILE-MP3:Texto desde archivo DOCX."
    assert list(reset_state_and_sandbox.glob(f"{backend_main.TEMP_DIR_PREFIX}*")) == []
    assert backend_main.conversion_cache.stats()["entries"] == 0

    # 2. File whose extracted text exceeds 15,000 characters -> HTTP 413
    long_txt_upload = _make_upload_file("largo.txt", ("X" * 15005).encode("utf-8"), "text/plain")
    with patch("backend.main.create_locked_temp_dir", side_effect=sandboxed_create):
        with pytest.raises(HTTPException) as exc_info:
            backend_main.convert_file_to_mp3(
                request=_make_request("/api/tts/file"),
                file=long_txt_upload,
                language="es",
                engine="edge",
                voice=None,
            )
        assert exc_info.value.status_code == 413

    # 3. Synthesis failure during file upload cleans up temporary directory
    valid_txt_upload = _make_upload_file("valido.txt", b"Hola documento", "text/plain")
    with patch("backend.main.create_locked_temp_dir", side_effect=sandboxed_create), \
         patch("backend.main.text_to_speech", side_effect=ConnectionError("TTS down")):
        with pytest.raises(HTTPException) as exc_info:
            backend_main.convert_file_to_mp3(
                request=_make_request("/api/tts/file"),
                file=valid_txt_upload,
                language="es",
                engine="edge",
                voice=None,
            )
        assert exc_info.value.status_code == 502
    assert list(reset_state_and_sandbox.glob(f"{backend_main.TEMP_DIR_PREFIX}*")) == []


# =============================================================================
# 4. Conversion Cache Tests (Hits, Misses, Option Isolation, TTL, Size Limits)
# =============================================================================


def test_conversion_cache_unit_ttl_lru_and_size_bounds(reset_state_and_sandbox: Path):
    fake_now = [1000.0]
    cache_dir = reset_state_and_sandbox / "unit_cache"
    cache = ConversionCache(
        cache_dir=cache_dir,
        ttl_seconds=60.0,
        max_entries=2,
        max_total_bytes=250,
        clock=lambda: fake_now[0],
    )

    src1 = reset_state_and_sandbox / "src1.mp3"
    src2 = reset_state_and_sandbox / "src2.mp3"
    src3 = reset_state_and_sandbox / "src3.mp3"
    dest = reset_state_and_sandbox / "dest.mp3"

    src1.write_bytes(b"A" * 100)
    src2.write_bytes(b"B" * 100)
    src3.write_bytes(b"C" * 100)

    k1 = build_url_cache_key("https://example.com/1", "edge", "es", "es-ES-ElviraNeural")
    k2 = build_url_cache_key("https://example.com/2", "edge", "es", "es-ES-ElviraNeural")
    k3 = build_url_cache_key("https://example.com/3", "edge", "es", "es-ES-ElviraNeural")

    assert cache.put_from_file(k1, src1) is True
    assert cache.put_from_file(k2, src2) is True
    assert cache.stats()["entries"] == 2

    # Access k1 so k2 becomes the least-recently-used entry
    fake_now[0] += 5.0
    assert cache.copy_to_destination(k1, dest) is True
    assert dest.read_bytes() == b"A" * 100

    # Adding k3 must evict k2 (since max_entries=2 and max_total_bytes=250)
    assert cache.put_from_file(k3, src3) is True
    assert cache.copy_to_destination(k2, dest) is False
    assert cache.copy_to_destination(k1, dest) is True
    assert cache.copy_to_destination(k3, dest) is True

    # Advance clock past TTL (60s) -> all entries expire
    fake_now[0] += 70.0
    assert cache.copy_to_destination(k1, dest) is False
    assert cache.stats()["entries"] == 0

    # Invalid cache keys (e.g. traversal attempt) are safely rejected
    assert cache.copy_to_destination("../../etc/passwd", dest) is False
    assert cache.put_from_file("../bad_key", src1) is False


def test_endpoint_url_cache_hit_skips_extraction_and_isolates_options(
    reset_state_and_sandbox: Path,
):
    orig_create = backend_main.create_locked_temp_dir

    def sandboxed_create():
        return orig_create(temp_root=reset_state_and_sandbox)

    extract_calls = 0
    tts_calls = 0

    def counting_extract(url: str) -> str:
        nonlocal extract_calls
        extract_calls += 1
        return f"Extracted content from {url}"

    def counting_tts(
        text: str,
        output_path: str,
        language: str = "es",
        engine: str = "edge",
        voice: str = None,
    ) -> str:
        nonlocal tts_calls
        tts_calls += 1
        Path(output_path).write_bytes(
            f"AUDIO:{engine}:{language}:{voice or 'default'}".encode("utf-8")
        )
        return output_path

    with patch("backend.main.create_locked_temp_dir", side_effect=sandboxed_create), \
         patch("backend.main.extract_article", side_effect=counting_extract), \
         patch("backend.main.text_to_speech", side_effect=counting_tts):
        # 1st call: Cache MISS
        req_es = TTSRequest(
            url="https://example.com/cached-story",
            engine="edge",
            language="es",
            voice="es-ES-ElviraNeural",
        )
        resp1 = backend_main.convert_article_to_mp3(req_es, _make_request())
        assert resp1.headers.get("X-Cache") == "MISS"
        body1 = _consume_asgi_response(resp1)
        assert body1 == b"AUDIO:edge:es:default"
        assert extract_calls == 1
        assert tts_calls == 1

        # 2nd call with identical (url, engine, language, voice): Cache HIT!
        resp2 = backend_main.convert_article_to_mp3(req_es, _make_request())
        assert resp2.headers.get("X-Cache") == "HIT"
        body2 = _consume_asgi_response(resp2)
        assert body2 == b"AUDIO:edge:es:default"
        # Neither extract_article nor text_to_speech was called again
        assert extract_calls == 1
        assert tts_calls == 1

        # 3rd call with a different voice ('es-ES-AlvaroNeural'): Cache MISS!
        req_alvaro = TTSRequest(
            url="https://example.com/cached-story",
            engine="edge",
            language="es",
            voice="es-ES-AlvaroNeural",
        )
        resp3 = backend_main.convert_article_to_mp3(req_alvaro, _make_request())
        assert resp3.headers.get("X-Cache") == "MISS"
        body3 = _consume_asgi_response(resp3)
        assert body3 == b"AUDIO:edge:es:es-ES-AlvaroNeural"
        assert extract_calls == 2
        assert tts_calls == 2


def test_frontend_three_input_modes_and_render_cold_start_contracts():
    project_root = Path(__file__).resolve().parent.parent
    app_jsx = (project_root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    form_jsx = (
        project_root / "frontend" / "src" / "components" / "ConversionForm.jsx"
    ).read_text(encoding="utf-8")
    banner_jsx = (
        project_root / "frontend" / "src" / "components" / "StatusBanner.jsx"
    ).read_text(encoding="utf-8")

    # 3 input mode tabs in ConversionForm.jsx
    assert 'id="tab-mode-url"' in form_jsx
    assert 'id="tab-mode-text"' in form_jsx
    assert 'id="tab-mode-file"' in form_jsx
    assert "Eliminar archivo" in form_jsx

    # Render cold-start message in StatusBanner.jsx and ConversionForm.jsx
    expected_wakeup_msg = (
        "El servidor se está iniciando. Puede tardar unos segundos; estamos preparando la conversión."
    )
    assert expected_wakeup_msg in banner_jsx
    assert expected_wakeup_msg in form_jsx

    # Endpoints for all 3 modes in App.jsx
    assert '"/api/tts"' in app_jsx
    assert '"/api/tts/text"' in app_jsx
    assert '"/api/tts/file"' in app_jsx


# =============================================================================
# 5. CORS & Cloudflare Pages Production Origin Tests
# =============================================================================


def _run_full_asgi_request(
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]],
    body: bytes = b"",
) -> tuple[int, dict[str, str], bytes]:
    """Execute an HTTP request through the full FastAPI + CORSMiddleware ASGI stack."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": headers,
        "client": ("203.0.113.77", 54321),
        "server": ("text-to-speech-api-yfia.onrender.com", 443),
    }
    sent_events: list[dict] = []
    body_consumed = False

    async def receive() -> dict:
        nonlocal body_consumed
        if not body_consumed:
            body_consumed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent_events.append(message)

    asyncio.run(backend_main.app(scope, receive, send))

    start_msg = next(m for m in sent_events if m["type"] == "http.response.start")
    status_code = start_msg["status"]
    resp_headers = {
        k.decode("latin-1").lower(): v.decode("latin-1")
        for k, v in start_msg.get("headers", [])
    }
    resp_body = b"".join(
        m.get("body", b"") for m in sent_events if m["type"] == "http.response.body"
    )
    return status_code, resp_headers, resp_body


def test_cors_origin_normalization_and_default_cloudflare_inclusion():
    """Verify origin parsing strips trailing slashes/spaces/quotes and includes Cloudflare Pages origin."""
    assert (
        backend_main.normalize_origin("  https://text-to-speech-3ir.pages.dev/  ")
        == "https://text-to-speech-3ir.pages.dev"
    )
    assert (
        backend_main.normalize_origin("'https://TEXT-TO-SPEECH-3IR.pages.dev///'")
        == "https://text-to-speech-3ir.pages.dev"
    )
    assert backend_main.normalize_origin("*") == ""

    # Even if FRONTEND_ORIGINS in Render only contains localhost, the production Cloudflare origin is included
    origins = backend_main.get_allowed_origins(
        "http://localhost:5173/, http://127.0.0.1:5173/ , https://custom.pages.dev/ "
    )
    assert "https://text-to-speech-3ir.pages.dev" in origins
    assert "http://localhost:5173" in origins
    assert "http://127.0.0.1:5173" in origins
    assert "https://custom.pages.dev" in origins
    assert "*" not in origins
    assert all(not o.endswith("/") for o in origins)


def test_cors_cloudflare_origin_get_post_and_options_preflight(
    reset_state_and_sandbox: Path,
):
    """Reproduce exact Cloudflare Pages origin on GET /api/languages, OPTIONS preflight, and POST /api/tts."""
    cf_origin = b"https://text-to-speech-3ir.pages.dev"

    # 1. GET /api/languages from Cloudflare Pages origin
    status_get, headers_get, _ = _run_full_asgi_request(
        method="GET",
        path="/api/languages",
        headers=[
            (b"origin", cf_origin),
            (b"accept", b"application/json"),
        ],
    )
    assert status_get == 200
    assert headers_get.get("access-control-allow-origin") == "https://text-to-speech-3ir.pages.dev"
    assert "access-control-allow-credentials" not in headers_get

    # 2. OPTIONS /api/tts preflight from Cloudflare Pages origin
    status_opt, headers_opt, _ = _run_full_asgi_request(
        method="OPTIONS",
        path="/api/tts",
        headers=[
            (b"origin", cf_origin),
            (b"access-control-request-method", b"POST"),
            (b"access-control-request-headers", b"content-type, accept"),
        ],
    )
    assert status_opt == 200
    assert headers_opt.get("access-control-allow-origin") == "https://text-to-speech-3ir.pages.dev"
    allow_methods = headers_opt.get("access-control-allow-methods", "")
    assert "GET" in allow_methods and "POST" in allow_methods and "OPTIONS" in allow_methods
    assert "access-control-allow-credentials" not in headers_opt

    # 3. POST /api/tts/text from Cloudflare Pages origin
    orig_create = backend_main.create_locked_temp_dir

    def sandboxed_create():
        return orig_create(temp_root=reset_state_and_sandbox)

    def fake_tts(text: str, output_path: str, language: str = "es") -> str:
        Path(output_path).write_bytes(b"MP3-CORS-OK")
        return output_path

    with patch("backend.main.create_locked_temp_dir", side_effect=sandboxed_create), \
         patch("backend.main.text_to_speech", side_effect=fake_tts):
        status_post, headers_post, body_post = _run_full_asgi_request(
            method="POST",
            path="/api/tts/text",
            headers=[
                (b"origin", cf_origin),
                (b"content-type", b"application/json"),
                (b"accept", b"audio/mpeg"),
            ],
            body=b'{"text":"Hola desde Cloudflare Pages","engine":"edge","language":"es"}',
        )

    assert status_post == 200
    assert headers_post.get("access-control-allow-origin") == "https://text-to-speech-3ir.pages.dev"
    assert body_post == b"MP3-CORS-OK"

    # 4. Unauthorized external origin must NOT receive Access-Control-Allow-Origin
    status_bad, headers_bad, _ = _run_full_asgi_request(
        method="GET",
        path="/api/languages",
        headers=[(b"origin", b"https://untrusted-site.example.com")],
    )
    assert status_bad == 200
    assert "access-control-allow-origin" not in headers_bad


