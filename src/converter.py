"""Module responsible for converting text content into MP3 audio files using Edge TTS or ElevenLabs."""

import asyncio
from contextlib import suppress
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Optional

from aiohttp import ClientError
import edge_tts
from edge_tts.exceptions import EdgeTTSException
import requests
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    RequestException,
    Timeout as RequestsTimeout,
)

ENGINE_EDGE: str = "edge"
ENGINE_ELEVENLABS: str = "elevenlabs"
DEFAULT_ENGINE: str = ENGINE_EDGE
SUPPORTED_ENGINES: tuple[str, ...] = (ENGINE_EDGE, ENGINE_ELEVENLABS)

SUPPORTED_VOICES: dict[str, str] = {
    "es": "es-ES-ElviraNeural",
    "en": "en-US-AriaNeural",
    "fr": "fr-FR-DeniseNeural",
}

EDGE_VOICES_BY_LANGUAGE: dict[str, list[dict[str, str]]] = {
    "es": [
        {"id": "es-ES-ElviraNeural", "name": "Elvira Neural (Femenina)"},
        {"id": "es-ES-AlvaroNeural", "name": "Álvaro Neural (Masculina)"},
    ],
    "en": [
        {"id": "en-US-AriaNeural", "name": "Aria Neural (Female)"},
        {"id": "en-US-GuyNeural", "name": "Guy Neural (Male)"},
    ],
    "fr": [
        {"id": "fr-FR-DeniseNeural", "name": "Denise Neural (Féminine)"},
        {"id": "fr-FR-HenriNeural", "name": "Henri Neural (Masculine)"},
    ],
}

ELEVENLABS_MODEL_ID: str = "eleven_v4"
ELEVENLABS_OUTPUT_FORMAT: str = "mp3_44100_128"
ELEVENLABS_TTS_URL_TEMPLATE: str = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_VOICES_URL: str = "https://api.elevenlabs.io/v1/voices"
ELEVENLABS_REQUEST_TIMEOUT: tuple[int, int] = (10, 120)
ELEVENLABS_VOICES_TIMEOUT: int = 4
ELEVENLABS_VOICES_CACHE_TTL_SECONDS: float = 300.0

ELEVENLABS_QUOTA_NOTICE: str = (
    "ElevenLabs utiliza una cuota gratuita limitada de 10.000 créditos mensuales. "
    "Cada conversión consume créditos según la longitud del texto. "
    "Cuando se agote la cuota, podrás seguir utilizando Edge TTS sin coste."
)

ELEVENLABS_PREMADE_VOICES: tuple[dict[str, str], ...] = (
    {"id": "EXAVITQu4vr4xnSDxMaL", "name": "Sarah (Multilingüe · Femenina)"},
    {"id": "Xb7hH8MSUJpSbSDYk0k2", "name": "Alice (Multilingüe · Femenina)"},
    {"id": "FGY2WhTYpPnrIDTdsKH5", "name": "Laura (Multilingüe · Femenina)"},
    {"id": "XrExE9yKIg1WjnnlVkGX", "name": "Matilda (Multilingüe · Femenina)"},
    {"id": "CwhRBWXzGAHq8TQ4Fs17", "name": "Roger (Multilingüe · Masculina)"},
    {"id": "JBFqnCBsd6RMkjVDRZzb", "name": "George (Multilingüe · Masculina)"},
    {"id": "TX3LPaxmHKxFdv7VOQHJ", "name": "Liam (Multilingüe · Masculina)"},
    {"id": "IKne3meq5aSn9XLyUdCD", "name": "Charlie (Multilingüe · Masculina)"},
)

ELEVENLABS_DEFAULT_VOICE_BY_LANGUAGE: dict[str, str] = {
    "es": "EXAVITQu4vr4xnSDxMaL",
    "en": "Xb7hH8MSUJpSbSDYk0k2",
    "fr": "FGY2WhTYpPnrIDTdsKH5",
}

DEFAULT_LANGUAGE: str = "es"
DEFAULT_RATE: str = "+0%"
DEFAULT_PITCH: str = "+0Hz"

_VOICES_CACHE_LOCK = threading.Lock()
_CACHED_ELEVENLABS_VOICES: Optional[list[dict[str, str]]] = None
_CACHED_ELEVENLABS_TIMESTAMP: Optional[float] = None


class ElevenLabsError(Exception):
    """Specific exception for ElevenLabs synthesis errors with safe user-facing messages."""

    def __init__(
        self,
        message: str,
        status_code: int = 502,
        error_code: str = "elevenlabs_error",
    ) -> None:
        super().__init__(message)
        self.user_message = message
        self.status_code = status_code
        self.error_code = error_code


def _read_key_from_dotenv(key_name: str, env_path: Optional[Path] = None) -> str:
    """Read a variable from the local .env file without logging or printing its value."""
    target = env_path or (Path(__file__).resolve().parent.parent / ".env")
    if not target.is_file():
        return ""
    try:
        for raw_line in target.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() != key_name:
                continue
            cleaned = v.strip()
            if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
                cleaned = cleaned[1:-1].strip()
            return cleaned
    except OSError:
        return ""
    return ""


def get_elevenlabs_api_key(env_path: Optional[Path] = None) -> str:
    """Return ELEVENLABS_API_KEY from environment or local .env file (never hardcoded)."""
    if "ELEVENLABS_API_KEY" in os.environ:
        return os.environ["ELEVENLABS_API_KEY"].strip()
    return _read_key_from_dotenv("ELEVENLABS_API_KEY", env_path=env_path)


def is_elevenlabs_configured(env_path: Optional[Path] = None) -> bool:
    """Return True if a non-empty ELEVENLABS_API_KEY is available."""
    return bool(get_elevenlabs_api_key(env_path=env_path))


def clear_elevenlabs_voices_cache() -> None:
    """Reset the cached ElevenLabs voices list."""
    global _CACHED_ELEVENLABS_VOICES, _CACHED_ELEVENLABS_TIMESTAMP
    with _VOICES_CACHE_LOCK:
        _CACHED_ELEVENLABS_VOICES = None
        _CACHED_ELEVENLABS_TIMESTAMP = None


def fetch_available_elevenlabs_voices(
    use_cache: bool = True,
) -> list[dict[str, str]]:
    """Verify and return available official ElevenLabs voices.

    When ELEVENLABS_API_KEY is configured, queries GET /v1/voices from the backend
    to verify real availability of the predefined voices in the user's account.
    Falls back safely to ELEVENLABS_PREMADE_VOICES if unconfigured or unreachable.
    """
    global _CACHED_ELEVENLABS_VOICES, _CACHED_ELEVENLABS_TIMESTAMP
    fallback_voices = [dict(item) for item in ELEVENLABS_PREMADE_VOICES]

    api_key = get_elevenlabs_api_key()
    if not api_key:
        return fallback_voices

    now = time.monotonic()
    if use_cache:
        with _VOICES_CACHE_LOCK:
            if (
                _CACHED_ELEVENLABS_VOICES is not None
                and _CACHED_ELEVENLABS_TIMESTAMP is not None
                and (now - _CACHED_ELEVENLABS_TIMESTAMP) < ELEVENLABS_VOICES_CACHE_TTL_SECONDS
            ):
                return [dict(v) for v in _CACHED_ELEVENLABS_VOICES]

    try:
        response = requests.get(
            ELEVENLABS_VOICES_URL,
            headers={
                "xi-api-key": api_key,
                "Accept": "application/json",
            },
            timeout=ELEVENLABS_VOICES_TIMEOUT,
        )
        if not response.ok:
            return fallback_voices

        payload = response.json()
        raw_voices = payload.get("voices") if isinstance(payload, dict) else None
        if not isinstance(raw_voices, list) or not raw_voices:
            return fallback_voices

        remote_ids = {
            str(item.get("voice_id", "")).strip()
            for item in raw_voices
            if isinstance(item, dict) and str(item.get("voice_id", "")).strip()
        }

        verified = [
            dict(voice)
            for voice in ELEVENLABS_PREMADE_VOICES
            if voice["id"] in remote_ids
        ]

        if not verified:
            verified = fallback_voices

        with _VOICES_CACHE_LOCK:
            _CACHED_ELEVENLABS_VOICES = verified
            _CACHED_ELEVENLABS_TIMESTAMP = time.monotonic()

        return [dict(v) for v in verified]
    except (RequestException, ValueError, KeyError, TypeError):
        return fallback_voices


def validate_engine(engine: str) -> str:
    """Validate the speech synthesis engine identifier ('edge' or 'elevenlabs')."""
    if not isinstance(engine, str):
        raise TypeError("The engine must be a string.")

    cleaned = engine.strip().lower()
    if not cleaned:
        raise ValueError("The engine cannot be empty.")

    if cleaned not in SUPPORTED_ENGINES:
        supported_list = ", ".join(f"'{item}'" for item in SUPPORTED_ENGINES)
        raise ValueError(
            f"Unsupported engine '{engine.strip()}'. Supported engines: {supported_list}."
        )
    return cleaned


def validate_text(text: str) -> str:
    """Validate that the input text is a non-empty string.

    Args:
        text: The text content to validate.

    Returns:
        The stripped, non-empty text string.

    Raises:
        TypeError: If the text is not a string.
        ValueError: If the text is empty or contains only whitespace.
    """
    if not isinstance(text, str):
        raise TypeError("The text must be a string.")

    cleaned_text = text.strip()
    if not cleaned_text:
        raise ValueError("The text to convert cannot be empty.")

    return cleaned_text


def validate_language(language: str) -> str:
    """Validate the language code and return its corresponding default Edge TTS neural voice.

    Args:
        language: The language code to validate ('es', 'en', or 'fr').

    Returns:
        The fixed Edge TTS neural voice identifier for the requested language.

    Raises:
        TypeError: If the language code is not a string.
        ValueError: If the language code is empty or not in SUPPORTED_VOICES.
    """
    if not isinstance(language, str):
        raise TypeError("The language code must be a string.")

    cleaned_language = language.strip().lower()
    if not cleaned_language:
        raise ValueError("The language code cannot be empty.")

    if cleaned_language not in SUPPORTED_VOICES:
        supported_list = ", ".join(f"'{code}'" for code in SUPPORTED_VOICES)
        raise ValueError(
            f"Unsupported language code '{language.strip()}'. "
            f"Supported languages: {supported_list}."
        )

    return SUPPORTED_VOICES[cleaned_language]


def validate_engine_voice(
    engine: str,
    language: str,
    voice: Optional[str] = None,
) -> str:
    """Validate the (engine, language, voice) combination and return a valid voice identifier.

    - For 'edge', ensures the voice belongs to EDGE_VOICES_BY_LANGUAGE[language]
      (defaulting to SUPPORTED_VOICES[language] when voice is None or empty).
    - For 'elevenlabs', ensures the voice belongs to the official ELEVENLABS_PREMADE_VOICES
      (defaulting to ELEVENLABS_DEFAULT_VOICE_BY_LANGUAGE[language] when voice is None or empty).
    """
    validated_engine = validate_engine(engine)
    default_edge_voice = validate_language(language)
    cleaned_language = language.strip().lower()

    if validated_engine == ENGINE_EDGE:
        allowed_voices = EDGE_VOICES_BY_LANGUAGE[cleaned_language]
        allowed_ids = {item["id"] for item in allowed_voices}
        if voice is None or not str(voice).strip():
            return default_edge_voice
        if not isinstance(voice, str):
            raise TypeError("The voice identifier must be a string.")
        candidate = voice.strip()
        if candidate not in allowed_ids:
            allowed_str = ", ".join(f"'{item['id']}'" for item in allowed_voices)
            raise ValueError(
                f"Voice '{candidate}' is not compatible with engine 'edge' and language "
                f"'{cleaned_language}'. Allowed voices: {allowed_str}."
            )
        return candidate

    # validated_engine == ENGINE_ELEVENLABS
    allowed_elevenlabs_ids = {item["id"] for item in ELEVENLABS_PREMADE_VOICES}
    default_elevenlabs_voice = ELEVENLABS_DEFAULT_VOICE_BY_LANGUAGE.get(
        cleaned_language,
        ELEVENLABS_PREMADE_VOICES[0]["id"],
    )
    if voice is None or not str(voice).strip():
        return default_elevenlabs_voice
    if not isinstance(voice, str):
        raise TypeError("The voice identifier must be a string.")
    candidate = voice.strip()
    if candidate not in allowed_elevenlabs_ids:
        allowed_str = ", ".join(f"'{item['id']}'" for item in ELEVENLABS_PREMADE_VOICES)
        raise ValueError(
            f"Voice '{candidate}' is not a valid predefined ElevenLabs voice. "
            f"Allowed voices: {allowed_str}."
        )
    return candidate


def get_engines_catalog(
    language_names: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    """Build the full dynamic catalog of engines, languages, and compatible voices."""
    names = language_names or {
        "es": "Español",
        "en": "English",
        "fr": "Français",
    }

    edge_languages: list[dict[str, Any]] = []
    for code, default_voice in SUPPORTED_VOICES.items():
        voices_for_lang = [
            {
                "id": item["id"],
                "name": item["name"],
                "language": code,
                "engine": ENGINE_EDGE,
            }
            for item in EDGE_VOICES_BY_LANGUAGE.get(code, [])
        ]
        edge_languages.append(
            {
                "code": code,
                "name": names.get(code, code.upper()),
                "default_voice": default_voice,
                "voices": voices_for_lang,
            }
        )

    elevenlabs_available = fetch_available_elevenlabs_voices(use_cache=True)
    available_ids = {item["id"] for item in elevenlabs_available}

    elevenlabs_languages: list[dict[str, Any]] = []
    for code in SUPPORTED_VOICES:
        preferred_default = ELEVENLABS_DEFAULT_VOICE_BY_LANGUAGE.get(
            code,
            elevenlabs_available[0]["id"],
        )
        default_voice_id = (
            preferred_default
            if preferred_default in available_ids
            else elevenlabs_available[0]["id"]
        )
        # Put the language's default voice first for convenience in the UI
        ordered_voices = sorted(
            elevenlabs_available,
            key=lambda item: (0 if item["id"] == default_voice_id else 1),
        )
        voices_for_lang = [
            {
                "id": item["id"],
                "name": item["name"],
                "language": code,
                "engine": ENGINE_ELEVENLABS,
            }
            for item in ordered_voices
        ]
        elevenlabs_languages.append(
            {
                "code": code,
                "name": names.get(code, code.upper()),
                "default_voice": default_voice_id,
                "voices": voices_for_lang,
            }
        )

    return [
        {
            "id": ENGINE_EDGE,
            "name": "Edge TTS (Gratuito)",
            "description": "Síntesis neuronal gratuita mediante Microsoft Edge TTS.",
            "configured": True,
            "requires_api_key": False,
            "model_id": "edge-neural",
            "quota_notice": None,
            "default_language": DEFAULT_LANGUAGE,
            "languages": edge_languages,
        },
        {
            "id": ENGINE_ELEVENLABS,
            "name": "ElevenLabs (Eleven v4)",
            "description": "Síntesis neuronal expresiva con el modelo Eleven v4 de ElevenLabs.",
            "configured": is_elevenlabs_configured(),
            "requires_api_key": True,
            "model_id": ELEVENLABS_MODEL_ID,
            "quota_notice": ELEVENLABS_QUOTA_NOTICE,
            "default_language": DEFAULT_LANGUAGE,
            "languages": elevenlabs_languages,
        },
    ]


def prepare_output_path(output_path: str) -> Path:
    """Validate the destination path and ensure its parent directory exists.

    Args:
        output_path: Destination file path for the MP3 file.

    Returns:
        The resolved absolute Path object for the target MP3 file.

    Raises:
        TypeError: If the output path is not a string.
        ValueError: If the path is empty, points to a directory, or lacks a valid '.mp3' filename.
        OSError: If the parent directory cannot be created.
    """
    if not isinstance(output_path, str):
        raise TypeError("The output path must be a string.")

    cleaned_path = output_path.strip()
    if not cleaned_path:
        raise ValueError("The output path cannot be empty.")

    path = Path(cleaned_path).expanduser()
    if path.exists() and path.is_dir():
        raise ValueError(f"The output path '{cleaned_path}' points to a directory, not a file.")

    if path.suffix.lower() != ".mp3" or not path.stem.strip(" ."):
        raise ValueError(
            f"Invalid output path '{cleaned_path}'. Must include a valid filename ending in '.mp3'."
        )

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"Failed to create the output directory '{path.parent}': {exc}"
        ) from exc

    return path.resolve()


async def _synthesize_audio(
    text: str,
    output_file: Path,
    voice: str,
) -> None:
    """Synthesize speech asynchronously with Edge TTS and save to output_file.

    Args:
        text: Validated text content to synthesize.
        output_file: Path to the temporary file where audio bytes will be written.
        voice: Edge TTS neural voice identifier.
    """
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=DEFAULT_RATE,
        pitch=DEFAULT_PITCH,
    )
    await communicate.save(str(output_file))


def _classify_elevenlabs_http_error(response: requests.Response) -> ElevenLabsError:
    """Parse an ElevenLabs error response into a safe, descriptive ElevenLabsError."""
    status_code = response.status_code
    detail_status = ""
    detail_message = ""

    with suppress(ValueError, TypeError, KeyError):
        payload = response.json()
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, dict):
                detail_status = str(detail.get("status") or "").strip().lower()
                detail_message = str(detail.get("message") or "").strip().lower()
            elif isinstance(detail, str):
                detail_message = detail.strip().lower()

    combined_info = f"{detail_status} {detail_message}"

    if (
        "quota_exceeded" in combined_info
        or "quota" in combined_info
        or "credit" in combined_info
        or "character limit" in combined_info
    ):
        return ElevenLabsError(
            message=(
                "Se ha agotado la cuota o los créditos disponibles en ElevenLabs. "
                "Puedes cambiar manualmente a Edge TTS para seguir generando audio sin coste."
            ),
            status_code=402,
            error_code="quota_exceeded",
        )

    if "detected_unusual_activity" in combined_info or "unusual activity" in combined_info:
        return ElevenLabsError(
            message=(
                "ElevenLabs ha restringido el uso del plan gratuito desde la dirección IP "
                "de este servidor. Puedes cambiar manualmente a Edge TTS para continuar sin coste."
            ),
            status_code=403,
            error_code="free_tier_restricted",
        )

    if status_code in (401, 403):
        return ElevenLabsError(
            message=(
                "La clave ELEVENLABS_API_KEY no es válida o no tiene permisos para realizar "
                "esta conversión. Revisa tu configuración o cambia manualmente a Edge TTS."
            ),
            status_code=401,
            error_code="api_key_invalid",
        )

    if status_code == 402:
        return ElevenLabsError(
            message=(
                "La conversión en ElevenLabs requiere créditos adicionales o un plan superior. "
                "Puedes seleccionar otra voz predefinida o cambiar manualmente a Edge TTS."
            ),
            status_code=402,
            error_code="payment_required",
        )

    if status_code == 429:
        return ElevenLabsError(
            message=(
                "Se ha alcanzado el límite de peticiones de ElevenLabs. "
                "Espera unos segundos antes de reintentar o cambia manualmente a Edge TTS."
            ),
            status_code=429,
            error_code="rate_limit",
        )

    if status_code in (400, 422):
        return ElevenLabsError(
            message=(
                "ElevenLabs rechazó los parámetros de síntesis solicitados. "
                "Comprueba el idioma y la voz seleccionados o cambia manualmente a Edge TTS."
            ),
            status_code=400,
            error_code="invalid_request",
        )

    return ElevenLabsError(
        message=(
            "El servidor de ElevenLabs ha devuelto un error interno al procesar el audio. "
            "Inténtalo de nuevo más tarde o cambia manualmente a Edge TTS."
        ),
        status_code=502,
        error_code="provider_error",
    )


def _synthesize_elevenlabs_audio(
    text: str,
    output_file: Path,
    voice_id: str,
    language: str,
) -> None:
    """Synthesize speech using the official ElevenLabs REST API (model eleven_v4)."""
    api_key = get_elevenlabs_api_key()
    if not api_key:
        raise ElevenLabsError(
            message=(
                "ElevenLabs no está configurado porque falta la variable ELEVENLABS_API_KEY. "
                "Añade tu clave en el archivo .env o cambia manualmente a Edge TTS."
            ),
            status_code=503,
            error_code="api_key_missing",
        )

    endpoint_url = ELEVENLABS_TTS_URL_TEMPLATE.format(voice_id=voice_id)
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": ELEVENLABS_MODEL_ID,
        "language_code": language,
    }

    try:
        with requests.post(
            endpoint_url,
            params={"output_format": ELEVENLABS_OUTPUT_FORMAT},
            headers=headers,
            json=payload,
            timeout=ELEVENLABS_REQUEST_TIMEOUT,
            stream=True,
        ) as response:
            if not response.ok:
                raise _classify_elevenlabs_http_error(response)

            with open(output_file, "wb") as file_handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        file_handle.write(chunk)
    except ElevenLabsError:
        raise
    except RequestsTimeout as exc:
        raise ElevenLabsError(
            message=(
                "La petición a ElevenLabs superó el tiempo máximo de espera (timeout). "
                "Inténtalo de nuevo o cambia manualmente a Edge TTS."
            ),
            status_code=504,
            error_code="timeout",
        ) from exc
    except RequestsConnectionError as exc:
        raise ElevenLabsError(
            message=(
                "No se pudo conectar con el servicio de ElevenLabs. "
                "Comprueba la conexión de red o cambia manualmente a Edge TTS."
            ),
            status_code=502,
            error_code="connection_error",
        ) from exc
    except RequestException as exc:
        raise ElevenLabsError(
            message=(
                "Se produjo un error de comunicación con ElevenLabs al generar el audio. "
                "Inténtalo más tarde o cambia manualmente a Edge TTS."
            ),
            status_code=502,
            error_code="request_error",
        ) from exc


def text_to_speech(
    text: str,
    output_path: str,
    language: str = DEFAULT_LANGUAGE,
    engine: str = DEFAULT_ENGINE,
    voice: Optional[str] = None,
) -> str:
    """Convert text into an MP3 audio file using Edge TTS or ElevenLabs (eleven_v4).

    Audio is synthesized into a closed temporary file in the target directory
    and atomically moved to the destination only after verifying a non-empty MP3
    result, preventing incomplete or corrupted files on failure.

    Args:
        text: The text content to convert into speech.
        output_path: Destination file path for the generated MP3 audio.
        language: Language code ('es', 'en', or 'fr'; default: 'es').
        engine: Speech synthesis engine ('edge' or 'elevenlabs'; default: 'edge').
        voice: Optional voice identifier compatible with the selected engine and language.

    Returns:
        The absolute path to the generated MP3 file as a string.

    Raises:
        TypeError: If any argument is not of the expected string type.
        ValueError: If text, output_path, language, engine, or voice have invalid values.
        ElevenLabsError: If ElevenLabs fails due to missing/invalid key, quota, rate limit,
            timeout, connectivity, or upstream error.
        ConnectionError: If Edge TTS synthesis fails due to network, timeout, or WebSocket errors.
        OSError: If creating directories or writing the MP3 file to disk fails.
        RuntimeError: If the generated audio file is missing or empty.
    """
    validated_text = validate_text(text)
    validated_engine = validate_engine(engine)
    selected_voice = validate_engine_voice(
        engine=validated_engine,
        language=language,
        voice=voice,
    )
    cleaned_language = language.strip().lower()
    destination = prepare_output_path(output_path)

    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.stem}_",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)

        if validated_engine == ENGINE_ELEVENLABS:
            _synthesize_elevenlabs_audio(
                text=validated_text,
                output_file=temp_path,
                voice_id=selected_voice,
                language=cleaned_language,
            )
        else:
            asyncio.run(
                _synthesize_audio(
                    text=validated_text,
                    output_file=temp_path,
                    voice=selected_voice,
                )
            )

        if not temp_path.is_file() or temp_path.stat().st_size == 0:
            raise RuntimeError(
                f"Audio synthesis produced an empty file for '{destination}'."
            )

        temp_path.replace(destination)
    except ElevenLabsError:
        raise
    except (
        EdgeTTSException,
        ClientError,
        asyncio.TimeoutError,
        TimeoutError,
        ConnectionError,
    ) as exc:
        raise ConnectionError(
            f"Failed to synthesize audio with Edge TTS using voice '{selected_voice}': {exc}"
        ) from exc
    except OSError as exc:
        raise OSError(
            f"Failed to write the MP3 file at '{destination}': {exc}"
        ) from exc
    finally:
        if temp_path is not None:
            with suppress(OSError):
                temp_path.unlink(missing_ok=True)

    return str(destination)
