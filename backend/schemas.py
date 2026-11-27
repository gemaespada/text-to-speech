"""Pydantic schemas for request validation and API responses."""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.converter import (
    DEFAULT_ENGINE,
    DEFAULT_LANGUAGE,
    SUPPORTED_VOICES,
    validate_engine,
    validate_engine_voice,
)
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
    engine: str = Field(
        default=DEFAULT_ENGINE,
        description="Speech synthesis engine ('edge' or 'elevenlabs').",
    )
    voice: Optional[str] = Field(
        default=None,
        description="Optional voice identifier compatible with the selected engine and language.",
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

    @field_validator("engine")
    @classmethod
    def validate_engine_code(cls, value: str) -> str:
        """Ensure the synthesis engine is supported ('edge' or 'elevenlabs')."""
        return validate_engine(value)

    @model_validator(mode="after")
    def validate_voice_compatibility(self) -> "TTSRequest":
        """Ensure any provided voice identifier is valid for the selected engine and language."""
        if self.voice is not None and not self.voice.strip():
            self.voice = None
        if self.voice is not None:
            self.voice = validate_engine_voice(
                engine=self.engine,
                language=self.language,
                voice=self.voice,
            )
        return self


class HealthResponse(BaseModel):
    """Response schema for the health check endpoint."""

    status: str = "ok"


class LanguageItem(BaseModel):
    """Metadata for a single supported synthesis language."""

    code: str
    name: str
    voice: str


class VoiceOption(BaseModel):
    """Metadata for a single selectable voice within an engine and language."""

    id: str
    name: str
    language: str
    engine: str


class EngineLanguageVoices(BaseModel):
    """Language entry and its compatible voices for a specific TTS engine."""

    code: str
    name: str
    default_voice: str
    voices: list[VoiceOption]


class EngineCatalog(BaseModel):
    """Metadata and available languages/voices for a speech synthesis engine."""

    id: str
    name: str
    description: str
    configured: bool
    requires_api_key: bool
    model_id: Optional[str] = None
    quota_notice: Optional[str] = None
    default_language: str
    languages: list[EngineLanguageVoices]


class LanguagesResponse(BaseModel):
    """Response schema listing all supported synthesis languages and engine catalogs."""

    default: str
    languages: list[LanguageItem]
    default_engine: str = DEFAULT_ENGINE
    engines: list[EngineCatalog] = Field(default_factory=list)
