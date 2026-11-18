"""Module responsible for downloading and extracting content from web articles."""

from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

import nltk
from newspaper import Article
from newspaper.article import ArticleException
from requests.exceptions import RequestException


def validate_url(url: str) -> str:
    """Validate that the provided string is a well-formed HTTP or HTTPS URL.

    Args:
        url: The URL string to validate.

    Returns:
        The stripped, validated URL string.

    Raises:
        TypeError: If the URL is not a string.
        ValueError: If the URL is empty, contains whitespace, or lacks a valid scheme/domain.
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

    return cleaned_url


@lru_cache(maxsize=1)
def ensure_nltk_resources() -> None:
    """Ensure required NLTK tokenizers are available for article processing.

    Raises:
        RuntimeError: If a required NLTK resource is missing and cannot be downloaded.
    """
    for resource in ("punkt", "punkt_tab"):
        try:
            nltk.data.find(f"tokenizers/{resource}")
        except LookupError as exc:
            if not nltk.download(resource, quiet=True):
                raise RuntimeError(
                    f"Failed to download required NLTK resource '{resource}'."
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

    Args:
        url: The web address of the article to extract.

    Returns:
        The extracted and formatted text content (title and main body).

    Raises:
        TypeError: If the URL argument is not a string.
        ValueError: If the URL is invalid or the page contains no extractable text.
        ConnectionError: If downloading the article fails due to network or HTTP errors.
        RuntimeError: If parsing or NLP processing of the article fails.
    """
    validated_url = validate_url(url)
    ensure_nltk_resources()

    article = Article(validated_url)

    try:
        article.download()
        article.throw_if_not_downloaded_verbose()
    except (ArticleException, RequestException) as exc:
        raise ConnectionError(
            f"Failed to download the article from '{validated_url}': {exc}"
        ) from exc

    try:
        article.parse()
    except (ArticleException, ValueError, LookupError) as exc:
        raise RuntimeError(
            f"Failed to parse the article HTML at '{validated_url}': {exc}"
        ) from exc

    if not article.text or not article.text.strip():
        raise ValueError(
            f"No article text content could be extracted from '{validated_url}'."
        )

    try:
        article.nlp()
    except (ArticleException, LookupError) as exc:
        raise RuntimeError(
            f"Failed to process natural language for the article at '{validated_url}': {exc}"
        ) from exc

    return format_article_content(article.title, article.text)
