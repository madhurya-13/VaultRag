"""Conservative text cleaning for extracted PDF pages.

The cleaner only normalises presentation whitespace. It deliberately does not
remove punctuation, numbers, dates, identifiers or technical terms, because
those can change the meaning of a document and its embeddings.
"""

from __future__ import annotations

import re

_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_HORIZONTAL_SPACE = re.compile(r"[ \t\f\v]+")
_EXCESS_NEWLINES = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """Normalise whitespace while preserving paragraphs and meaning.

    Steps:
    1. Normalise Windows/old-Mac line endings.
    2. Remove zero-width characters and normalise non-breaking spaces.
    3. Collapse horizontal whitespace runs to one space.
    4. Trim leading and trailing whitespace from each line.
    5. Collapse runs of three or more newlines to one paragraph break.
    6. Trim outer whitespace.
    """
    if not text:
        return ""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _ZERO_WIDTH.sub("", normalized)
    normalized = normalized.replace("\u00a0", " ")
    normalized = _HORIZONTAL_SPACE.sub(" ", normalized)
    normalized = "\n".join(line.strip() for line in normalized.split("\n"))
    normalized = _EXCESS_NEWLINES.sub("\n\n", normalized)
    return normalized.strip()


def has_meaningful_text(text: str) -> bool:
    """Return True when text contains at least one non-whitespace character."""
    return bool(text and text.strip())
