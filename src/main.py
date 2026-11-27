"""Application entry point for the Text to Speech project."""

from pathlib import Path
import sys
from typing import Optional

from .converter import DEFAULT_ENGINE, ElevenLabsError, text_to_speech
from .extractor import extract_article

DEFAULT_OUTPUT_PATH: str = str(Path("output") / "article.mp3")
DEFAULT_LANGUAGE: str = "es"


def article_to_speech(
    url: str,
    output_path: str = DEFAULT_OUTPUT_PATH,
    language: str = DEFAULT_LANGUAGE,
    engine: str = DEFAULT_ENGINE,
    voice: Optional[str] = None,
) -> str:
    """Extract a web article from a URL and convert its content into an MP3 file.

    Args:
        url: The web address of the article to extract.
        output_path: Destination file path for the generated MP3 audio
            (default: 'output/article.mp3').
        language: Language code for speech synthesis (default: 'es').
        engine: Speech synthesis engine ('edge' or 'elevenlabs'; default: 'edge').
        voice: Optional voice identifier compatible with the selected engine and language.

    Returns:
        The absolute path to the generated MP3 file as a string.

    Raises:
        TypeError: If any argument is not a string.
        ValueError: If the URL, extracted text, output path, language, engine, or voice is invalid.
        ElevenLabsError: If ElevenLabs synthesis fails.
        ConnectionError: If article download or Edge TTS synthesis fails due to network errors.
        OSError: If creating the output directory or saving the MP3 file fails.
        RuntimeError: If article parsing/NLP fails or the generated audio is empty.
    """
    article_text = extract_article(url)
    if engine == DEFAULT_ENGINE and voice is None:
        return text_to_speech(
            text=article_text,
            output_path=output_path,
            language=language,
        )
    return text_to_speech(
        text=article_text,
        output_path=output_path,
        language=language,
        engine=engine,
        voice=voice,
    )


def prompt_user_inputs() -> tuple[str, str, str]:
    """Prompt the user via terminal for the URL, output path, and language code.

    Returns:
        A tuple containing (url, output_path, language).

    Raises:
        EOFError: If standard input is closed before all prompts are answered.
        KeyboardInterrupt: If the user interrupts input from the terminal.
    """
    url = input("Enter the article URL: ").strip()
    output_input = input(
        f"Enter output MP3 path [default: {DEFAULT_OUTPUT_PATH}]: "
    ).strip()
    language_input = input(
        f"Enter language code [default: {DEFAULT_LANGUAGE}]: "
    ).strip()

    output_path = output_input or DEFAULT_OUTPUT_PATH
    language = language_input or DEFAULT_LANGUAGE
    return url, output_path, language


def main() -> int:
    """Run the interactive command-line workflow for Text to Speech.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    try:
        url, output_path, language = prompt_user_inputs()
        print("Extracting article content and generating MP3 audio...")
        saved_path = article_to_speech(
            url=url,
            output_path=output_path,
            language=language,
        )
        print(f"MP3 audio successfully generated at: {saved_path}")
        return 0
    except (TypeError, ValueError) as exc:
        print(f"[Validation Error] {exc}", file=sys.stderr)
    except ElevenLabsError as exc:
        print(f"[ElevenLabs Error] {exc}", file=sys.stderr)
    except ConnectionError as exc:
        # Must precede OSError because ConnectionError is a subclass of OSError.
        print(f"[Network Error] {exc}", file=sys.stderr)
    except OSError as exc:
        print(f"[File System Error] {exc}", file=sys.stderr)
    except RuntimeError as exc:
        print(f"[Processing Error] {exc}", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("\nOperation cancelled by user.", file=sys.stderr)

    return 1


if __name__ == "__main__":
    sys.exit(main())
