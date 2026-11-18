"""Module responsible for converting text content into MP3 audio files using Edge TTS."""

import asyncio
from contextlib import suppress
from pathlib import Path
import tempfile
from typing import Optional

from aiohttp import ClientError
import edge_tts
from edge_tts.exceptions import EdgeTTSException

SUPPORTED_VOICES: dict[str, str] = {
    "es": "es-ES-ElviraNeural",
    "en": "en-US-AriaNeural",
    "fr": "fr-FR-DeniseNeural",
}

DEFAULT_LANGUAGE: str = "es"
DEFAULT_RATE: str = "+0%"
DEFAULT_PITCH: str = "+0Hz"


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
    """Validate the language code and return its corresponding Edge TTS neural voice.

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


def text_to_speech(
    text: str,
    output_path: str,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    """Convert text into an MP3 audio file using Microsoft Edge TTS neural voices.

    Audio is synthesized into a closed temporary file in the target directory
    and atomically moved to the destination only after verifying a non-empty MP3
    result, preventing incomplete or corrupted files on failure.

    Args:
        text: The text content to convert into speech.
        output_path: Destination file path for the generated MP3 audio.
        language: Language code ('es', 'en', or 'fr'; default: 'es').

    Returns:
        The absolute path to the generated MP3 file as a string.

    Raises:
        TypeError: If any argument is not of type string.
        ValueError: If text, output_path, or language have invalid values.
        ConnectionError: If speech synthesis fails due to network, timeout, or WebSocket errors.
        OSError: If creating directories or writing the MP3 file to disk fails.
        RuntimeError: If the generated audio file is missing or empty.
    """
    validated_text = validate_text(text)
    selected_voice = validate_language(language)
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
