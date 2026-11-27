"""Safe extraction and validation of text from local document uploads (PDF, DOCX, TXT, EPUB)."""

import io
from pathlib import Path, PurePosixPath
from typing import Optional
import xml.etree.ElementTree as ET
import zipfile

from bs4 import BeautifulSoup
import docx
from docx.opc.exceptions import PackageNotFoundError
from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError

SUPPORTED_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".docx", ".txt", ".epub"}
)

MAX_UPLOAD_FILE_BYTES: int = 10 * 1024 * 1024  # 10 MB maximum uploaded file size
MAX_ARCHIVE_UNCOMPRESSED_BYTES: int = 25 * 1024 * 1024  # 25 MB max uncompressed ZIP payload
MAX_ARCHIVE_ENTRIES: int = 1000

ALLOWED_MIME_TYPES_BY_EXTENSION: dict[str, frozenset[str]] = {
    ".pdf": frozenset(
        {
            "application/pdf",
            "application/x-pdf",
            "application/octet-stream",
            "binary/octet-stream",
            "",
        }
    ),
    ".docx": frozenset(
        {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/zip",
            "application/x-zip-compressed",
            "application/octet-stream",
            "binary/octet-stream",
            "",
        }
    ),
    ".txt": frozenset(
        {
            "text/plain",
            "application/octet-stream",
            "binary/octet-stream",
            "",
        }
    ),
    ".epub": frozenset(
        {
            "application/epub+zip",
            "application/zip",
            "application/x-zip-compressed",
            "application/octet-stream",
            "binary/octet-stream",
            "",
        }
    ),
}

_BINARY_SIGNATURES: tuple[bytes, ...] = (
    b"%PDF-",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"\x7fELF",
    b"MZ",
    b"\x1f\x8b",
)


class DocumentValidationError(ValueError):
    """Raised when an uploaded file fails extension, MIME, magic-byte, or integrity checks."""


class DocumentTooLargeError(ValueError):
    """Raised when an uploaded file exceeds MAX_UPLOAD_FILE_BYTES."""


class EmptyDocumentError(ValueError):
    """Raised when an uploaded file contains no extractable text."""


def sanitize_upload_filename(filename: Optional[str]) -> tuple[str, str]:
    """Sanitize a user-supplied filename and validate its extension.

    Strips any directory traversal components, rejects null bytes or empty names,
    and returns (safe_basename, lowercase_extension).
    """
    if not filename or not isinstance(filename, str):
        raise DocumentValidationError(
            "Debes proporcionar un archivo válido con nombre y extensión (.pdf, .docx, .txt o .epub)."
        )

    if "\x00" in filename:
        raise DocumentValidationError("El nombre del archivo contiene caracteres no válidos.")

    # Normalize backslashes and take only the final basename component
    normalized = filename.strip().replace("\\", "/")
    base_name = Path(normalized).name.strip()

    if not base_name or base_name in (".", "..") or base_name.startswith("."):
        raise DocumentValidationError(
            "El nombre del archivo no es válido. Selecciona un documento .pdf, .docx, .txt o .epub."
        )

    ext = Path(base_name).suffix.lower()
    if ext not in SUPPORTED_DOCUMENT_EXTENSIONS:
        supported_str = ", ".join(sorted(SUPPORTED_DOCUMENT_EXTENSIONS))
        raise DocumentValidationError(
            f"Formato de archivo no compatible ('{ext or 'sin extensión'}'). "
            f"Formatos admitidos: {supported_str}."
        )

    return base_name, ext


def validate_declared_mime_type(extension: str, content_type: Optional[str]) -> None:
    """Verify that the declared MIME type is compatible with the file extension."""
    raw_mime = (content_type or "").split(";", 1)[0].strip().lower()
    allowed = ALLOWED_MIME_TYPES_BY_EXTENSION.get(extension, frozenset())
    if raw_mime not in allowed:
        raise DocumentValidationError(
            f"El tipo MIME del archivo ('{raw_mime}') no coincide con la extensión '{extension}'."
        )


def normalize_extracted_text(text: str) -> str:
    """Clean and normalize paragraphs from extracted document text."""
    lines = [
        " ".join(line.split())
        for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    paragraphs: list[str] = []
    for line in lines:
        if line:
            paragraphs.append(line)
    return "\n\n".join(paragraphs).strip()


def _verify_safe_zip_structure(
    raw_bytes: bytes,
    format_label: str,
) -> zipfile.ZipFile:
    """Validate that raw_bytes is a safe ZIP archive without traversal or zip-bomb payloads."""
    if not raw_bytes.startswith(b"PK\x03\x04"):
        raise DocumentValidationError(
            f"El contenido real del archivo no corresponde a un documento {format_label} válido."
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocumentValidationError(
            f"El archivo {format_label} está dañado o corrupto y no se puede abrir."
        ) from exc

    infos = zf.infolist()
    if not infos:
        zf.close()
        raise DocumentValidationError(f"El archivo {format_label} está vacío o corrupto.")

    if len(infos) > MAX_ARCHIVE_ENTRIES:
        zf.close()
        raise DocumentValidationError(
            f"El archivo {format_label} contiene demasiados elementos internos."
        )

    total_uncompressed = 0
    for info in infos:
        member_name = info.filename.replace("\\", "/")
        posix_path = PurePosixPath(member_name)
        if (
            "\x00" in member_name
            or member_name.startswith("/")
            or ".." in posix_path.parts
        ):
            zf.close()
            raise DocumentValidationError(
                f"El archivo {format_label} contiene rutas internas no seguras."
            )
        if info.file_size < 0:
            zf.close()
            raise DocumentValidationError(
                f"El archivo {format_label} tiene metadatos de tamaño inválidos."
            )
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            zf.close()
            raise DocumentValidationError(
                f"El contenido descomprimido del archivo {format_label} supera el límite de seguridad."
            )

    return zf


def _safe_parse_xml(xml_bytes: bytes, format_label: str) -> ET.Element:
    """Parse XML bytes rejecting DOCTYPE/ENTITY declarations to prevent XXE/Billion Laughs."""
    upper_sample = xml_bytes[:4096].upper()
    if b"<!DOCTYPE" in upper_sample or b"<!ENTITY" in upper_sample:
        raise DocumentValidationError(
            f"El archivo {format_label} contiene declaraciones XML externas no permitidas."
        )
    try:
        return ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise DocumentValidationError(
            f"La estructura interna del archivo {format_label} está dañada o mal formada."
        ) from exc


def _extract_pdf_text(raw_bytes: bytes) -> str:
    """Validate PDF magic bytes and extract readable text from all pages."""
    leading = raw_bytes.lstrip()
    if not leading.startswith(b"%PDF-"):
        raise DocumentValidationError(
            "El contenido real del archivo no corresponde a un documento PDF válido."
        )

    try:
        reader = PdfReader(io.BytesIO(raw_bytes), strict=False)
        if reader.is_encrypted:
            raise DocumentValidationError(
                "El archivo PDF está protegido con contraseña y no se puede extraer su texto."
            )
        if len(reader.pages) == 0:
            raise EmptyDocumentError("El documento PDF no contiene páginas.")

        page_texts: list[str] = []
        for page in reader.pages:
            extracted = page.extract_text() or ""
            if extracted.strip():
                page_texts.append(extracted)
    except (DocumentValidationError, EmptyDocumentError):
        raise
    except (PdfReadError, PdfStreamError, ValueError, KeyError, IndexError, OSError, Exception) as exc:
        raise DocumentValidationError(
            "El archivo PDF está dañado o corrupto y no se pudo procesar."
        ) from exc

    cleaned = normalize_extracted_text("\n\n".join(page_texts))
    if not cleaned:
        raise EmptyDocumentError(
            "No se pudo extraer texto del PDF. Si es un documento escaneado basado en imágenes (OCR), "
            "no está soportado en esta versión."
        )
    return cleaned


def _extract_docx_text(raw_bytes: bytes) -> str:
    """Validate DOCX package structure and extract paragraph and table text."""
    with _verify_safe_zip_structure(raw_bytes, "DOCX") as zf:
        names = set(zf.namelist())
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise DocumentValidationError(
                "El archivo no tiene la estructura interna de un documento Word (.docx) válido."
            )
        # Validate XML safety on primary document payload
        _safe_parse_xml(zf.read("word/document.xml"), "DOCX")

    try:
        document = docx.Document(io.BytesIO(raw_bytes))
        blocks: list[str] = []
        for paragraph in document.paragraphs:
            if paragraph.text and paragraph.text.strip():
                blocks.append(paragraph.text)

        for table in document.tables:
            for row in table.rows:
                cells = [
                    cell.text.strip()
                    for cell in row.cells
                    if cell.text and cell.text.strip()
                ]
                if cells:
                    blocks.append(" · ".join(cells))
    except (PackageNotFoundError, ValueError, KeyError, OSError, Exception) as exc:
        raise DocumentValidationError(
            "El archivo DOCX está dañado o no tiene un formato válido."
        ) from exc

    cleaned = normalize_extracted_text("\n\n".join(blocks))
    if not cleaned:
        raise EmptyDocumentError("El documento DOCX no contiene texto legible.")
    return cleaned


def _extract_epub_text(raw_bytes: bytes) -> str:
    """Validate EPUB container structure and extract text from chapters in reading order."""
    with _verify_safe_zip_structure(raw_bytes, "EPUB") as zf:
        names = set(zf.namelist())
        if "META-INF/container.xml" not in names:
            raise DocumentValidationError(
                "El archivo no tiene la estructura interna de un libro EPUB válido."
            )

        container_root = _safe_parse_xml(zf.read("META-INF/container.xml"), "EPUB")
        opf_path: Optional[str] = None
        for elem in container_root.iter():
            if elem.tag.endswith("rootfile") and elem.attrib.get("full-path"):
                opf_path = elem.attrib["full-path"].strip().lstrip("/")
                break

        if not opf_path or opf_path not in names:
            raise DocumentValidationError(
                "El archivo EPUB no contiene un manifiesto de contenido (.opf) válido."
            )

        opf_root = _safe_parse_xml(zf.read(opf_path), "EPUB")
        opf_dir = PurePosixPath(opf_path).parent

        manifest_items: dict[str, str] = {}
        html_fallbacks: list[str] = []
        for elem in opf_root.iter():
            if elem.tag.endswith("item"):
                item_id = elem.attrib.get("id", "").strip()
                href = elem.attrib.get("href", "").strip()
                media_type = elem.attrib.get("media-type", "").strip().lower()
                if not href:
                    continue
                resolved_href = str((opf_dir / href).as_posix()).lstrip("./")
                if resolved_href not in names and href in names:
                    resolved_href = href
                if resolved_href in names:
                    if item_id:
                        manifest_items[item_id] = resolved_href
                    if (
                        "html" in media_type
                        or resolved_href.lower().endswith((".xhtml", ".html", ".htm"))
                    ):
                        html_fallbacks.append(resolved_href)

        spine_files: list[str] = []
        for elem in opf_root.iter():
            if elem.tag.endswith("itemref"):
                idref = elem.attrib.get("idref", "").strip()
                target_file = manifest_items.get(idref)
                if target_file and target_file not in spine_files:
                    spine_files.append(target_file)

        chapter_files = spine_files or html_fallbacks
        if not chapter_files:
            raise EmptyDocumentError("El archivo EPUB no contiene capítulos de texto legibles.")

        chapters_text: list[str] = []
        for chapter_path in chapter_files:
            if not chapter_path.lower().endswith((".xhtml", ".html", ".htm", ".xml")):
                continue
            raw_chapter = zf.read(chapter_path)
            soup = BeautifulSoup(raw_chapter, "html.parser")
            for ignored_tag in soup(["script", "style", "head", "noscript"]):
                ignored_tag.decompose()
            chapter_str = soup.get_text(separator="\n")
            if chapter_str and chapter_str.strip():
                chapters_text.append(chapter_str)

    cleaned = normalize_extracted_text("\n\n".join(chapters_text))
    if not cleaned:
        raise EmptyDocumentError("El archivo EPUB no contiene texto extraíble.")
    return cleaned


def _extract_txt_text(raw_bytes: bytes) -> str:
    """Validate that raw_bytes is genuine plain text (not a disguised binary) and decode it."""
    if b"\x00" in raw_bytes:
        raise DocumentValidationError(
            "El archivo .txt contiene bytes binarios nulos y no es un archivo de texto plano válido."
        )

    leading = raw_bytes.lstrip()
    for signature in _BINARY_SIGNATURES:
        if raw_bytes.startswith(signature) or leading.startswith(signature):
            raise DocumentValidationError(
                "El contenido real del archivo es binario y no corresponde a un documento .txt."
            )

    try:
        decoded = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            decoded = raw_bytes.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise DocumentValidationError(
                "No se pudo decodificar la codificación de caracteres del archivo .txt."
            ) from exc

    # Reject files dominated by non-printable control characters
    control_count = sum(
        1
        for ch in decoded
        if ord(ch) < 32 and ch not in ("\n", "\r", "\t")
    )
    if control_count > 0:
        raise DocumentValidationError(
            "El archivo .txt contiene caracteres de control binarios no válidos."
        )

    cleaned = normalize_extracted_text(decoded)
    if not cleaned:
        raise EmptyDocumentError("El archivo de texto (.txt) está vacío o solo contiene espacios.")
    return cleaned


def extract_text_from_document(
    filename: Optional[str],
    content_type: Optional[str],
    raw_bytes: bytes,
    max_file_bytes: int = MAX_UPLOAD_FILE_BYTES,
) -> str:
    """Validate and extract readable text from an uploaded PDF, DOCX, TXT, or EPUB file.

    Args:
        filename: Original filename provided by the upload.
        content_type: Declared MIME type from the upload header.
        raw_bytes: Complete binary content of the uploaded file.
        max_file_bytes: Maximum allowed file size in bytes (default: 10 MB).

    Returns:
        Cleaned, normalized text string extracted from the document.

    Raises:
        DocumentValidationError: If the filename, extension, MIME, magic bytes, or file structure is invalid.
        DocumentTooLargeError: If raw_bytes exceeds max_file_bytes.
        EmptyDocumentError: If the file is 0 bytes or contains no readable text.
    """
    _, ext = sanitize_upload_filename(filename)
    validate_declared_mime_type(ext, content_type)

    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise DocumentValidationError("El contenido del archivo no es válido.")

    if len(raw_bytes) == 0:
        raise EmptyDocumentError("El archivo seleccionado está vacío (0 bytes).")

    if len(raw_bytes) > max_file_bytes:
        max_mb = max_file_bytes // (1024 * 1024)
        raise DocumentTooLargeError(
            f"El archivo supera el tamaño máximo permitido de {max_mb} MB."
        )

    data = bytes(raw_bytes)
    if ext == ".pdf":
        return _extract_pdf_text(data)
    if ext == ".docx":
        return _extract_docx_text(data)
    if ext == ".epub":
        return _extract_epub_text(data)
    if ext == ".txt":
        return _extract_txt_text(data)

    raise DocumentValidationError(f"Formato de documento no soportado: '{ext}'.")
