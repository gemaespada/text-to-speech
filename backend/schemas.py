"""Pydantic schemas for request validation and API responses."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.converter import DEFAULT_LANGUAGE, SUPPORTED_VOICES
from src.extractor import validate_url

LANGUAGE_NAMES: dict[str, str] = {
    "es": "Español",
    "en": "English",
    "fr": "Français",
}

if set(LANGUAGE_NAMES.keys()) != set(SUPPORTED_VOICES.keys()):
    raise RuntimeError(
        "LANGUAGE_NAMES keys must match SUPPORTED_VOICES keys in src.converter."
    )


class TTSRequest(BaseModel):
    """Request payload for converting a web article into MP3 speech."""

    model_config = ConfigDict(str_strip_whitespace=True)

    url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="Public HTTP or HTTPS URL of the web article to convert (ports 80/443 only).",
    )
    language: str = Field(
        default=DEFAULT_LANGUAGE,
        description="Language code for speech synthesis ('es', 'en', or 'fr').",
    )

    @field_validator("url")
    @classmethod
    def validate_article_url(cls, value: str) -> str:
        """Validate the URL using src.extractor.validate_url (scheme, port, credentials, host/IP)."""
        return validate_url(value)

    @field_validator("language")
    @classmethod
    def validate_language_code(cls, value: str) -> str:
        """Ensure the language code is supported by src.converter.SUPPORTED_VOICES."""
        cleaned = value.strip().lower()
        if cleaned not in SUPPORTED_VOICES:
            supported_list = ", ".join(f"'{code}'" for code in SUPPORTED_VOICES)
            raise ValueError(
                f"Unsupported language code '{value}'. Supported languages: {supported_list}."
            )
        return cleaned


class HealthResponse(BaseModel):
    """Response schema for the health check endpoint."""

    status: str = "ok"


class LanguageItem(BaseModel):
    """Metadata for a single supported synthesis language."""

    code: str
    name: str
    voice: str


class LanguagesResponse(BaseModel):
    """Response schema listing all supported synthesis languages."""

    default: str
    languages: list[LanguageItem]
