"""Module responsible for converting text content into MP3 audio files."""

from functools import lru_cache
from pathlib import Path
import tempfile
from typing import Optional

from gtts import gTTS
from gtts.lang import tts_langs
from gtts.tts import gTTSError


@lru_cache(maxsize=1)
def _get_supported_languages() -> dict[str, str]:
    """Return a cached mapping of lowercase language codes to canonical gTTS codes."""
    return {code.lower(): code for code in tts_langs()}


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
    """Validate that the language code is supported by gTTS.

    Args:
        language: The language code to validate (e.g., 'es', 'en', 'zh-CN').

    Returns:
        The canonical gTTS language code.

    Raises:
        TypeError: If the language code is not a string.
        ValueError: If the language code is empty or not supported by gTTS.
    """
    if not isinstance(language, str):
        raise TypeError("The language code must be a string.")

    cleaned_language = language.strip().lower()
    if not cleaned_language:
        raise ValueError("The language code cannot be empty.")

    supported_languages = _get_supported_languages()
    if cleaned_language not in supported_languages:
        raise ValueError(
            f"Unsupported language code '{language.strip()}'. "
            f"Supported examples: 'es', 'en', 'fr', 'de', 'it', 'pt'."
        )

    return supported_languages[cleaned_language]


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


def text_to_speech(text: str, output_path: str, language: str = "es") -> str:
    """Convert text into an MP3 audio file using Google Text-to-Speech (gTTS).

    Audio is first written to a temporary file in the target directory and
    atomically moved to the destination only after successful synthesis,
    preventing incomplete or corrupted MP3 files on failure.

    Args:
        text: The text content to convert into speech.
        output_path: Destination file path for the generated MP3 audio.
        language: Language code for speech synthesis (default: 'es').

    Returns:
        The absolute path to the generated MP3 file as a string.

    Raises:
        TypeError: If any argument is not of type string.
        ValueError: If text, output_path, or language have invalid values.
        ConnectionError: If speech synthesis fails due to gTTS network or API errors.
        OSError: If creating directories or writing the MP3 file to disk fails.
        RuntimeError: If the generated audio data is empty.
    """
    validated_text = validate_text(text)
    validated_language = validate_language(language)
    destination = prepare_output_path(output_path)

    temp_path: Optional[Path] = None
    try:
        tts = gTTS(text=validated_text, lang=validated_language, slow=False)

        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.stem}_",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            tts.write_to_fp(temp_file)
            temp_file.flush()

        if temp_path.stat().st_size == 0:
            raise RuntimeError(
                f"Audio synthesis produced an empty file for '{destination}'."
            )

        temp_path.replace(destination)
    except gTTSError as exc:
        raise ConnectionError(
            f"Failed to synthesize audio with gTTS for language '{validated_language}': {exc}"
        ) from exc
    except OSError as exc:
        raise OSError(
            f"Failed to write the MP3 file at '{destination}': {exc}"
        ) from exc
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return str(destination)
