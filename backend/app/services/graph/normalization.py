"""Canonical names, identity keys and quote anchoring.

Pure functions only: no database, no model, no configuration. Everything here is
unit-tested directly, because a mistake in ``identity_key`` or
``quote_supported`` is a correctness *and* a security problem.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

MAX_NAME_CHARS = 255
MAX_IDENTITY_KEY_CHARS = 160
MAX_RELATION_CHARS = 64

_WHITESPACE = re.compile(r"\s+")
# Keep characters that carry meaning in legal/enterprise names: letters, digits,
# ampersand, dot, slash, hyphen and underscore. Everything else collapses to a
# space so "Acme, Inc." and "Acme Inc" normalize identically.
_DISALLOWED = re.compile(r"[^0-9a-z&./_\- ]+")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_EMPLOYEE_ID = re.compile(
    r"\b(?:employee|emp|staff|badge)\b[\s_#:\-]*(?:id|no|number)?[\s_#:\-]*"
    r"([A-Za-z0-9][A-Za-z0-9_\-]{3,})\b",
    re.IGNORECASE,
)
# A dot that is not part of a number is abbreviation punctuation: "Acme, Inc."
# must normalize identically to "Acme Inc".
_ABBREVIATION_DOT = re.compile(r"(?<!\d)\.(?!\d)")
_RELATION_SLUG = re.compile(r"[^a-z0-9]+")


def normalize_name(value: str) -> str:
    """Lower-case, de-accent, collapse punctuation and whitespace."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = _DISALLOWED.sub(" ", text)
    text = _ABBREVIATION_DOT.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:MAX_NAME_CHARS]


def normalize_quote(value: str) -> str:
    """Whitespace-collapsed, case-folded form used for substring checks."""
    if not value:
        return ""
    return _WHITESPACE.sub(" ", str(value)).strip().lower()


def quote_supported(quote: str, chunk_text: str) -> bool:
    """True when ``quote`` really occurs inside ``chunk_text``.

    This is the anti-hallucination gate: an extraction (especially an LLM one)
    may only cite text that actually exists in the source chunk. Comparison is
    case-insensitive and whitespace-insensitive, but never fuzzy.
    """
    normalized_quote = normalize_quote(quote)
    if not normalized_quote:
        return False
    return normalized_quote in normalize_quote(chunk_text)


def name_in_quote(name: str, quote: str) -> bool:
    """True when the entity name appears verbatim inside its own quote."""
    normalized = normalize_name(name)
    if not normalized:
        return False
    return normalized in normalize_name(quote)


def infer_anchor(text: str) -> str | None:
    """Extract a disambiguating anchor (email or employee id) from text.

    Only an anchor makes two same-name people provably the same person. Without
    one, PERSON nodes are never auto-merged.
    """
    if not text:
        return None
    email = _EMAIL.search(text)
    if email:
        return f"email:{email.group(0).lower()}"
    employee = _EMPLOYEE_ID.search(text)
    if employee:
        value = employee.group(1)
        # An identifier almost always contains a digit. Requiring one stops a
        # sentence such as "every employee must ..." from becoming an anchor
        # and collapsing two different people.
        if any(character.isdigit() for character in value):
            return f"employee:{value.lower()}"
    return None


def identity_key(node_type: str, name: str, anchor: str | None = None) -> str:
    """Stable merge identity for a node.

    Format: ``TYPE|anchor|value`` or ``TYPE|name|normalized name``. The anchor
    prefix is what prevents accidental homonym merges: "Alice Smith" mentioned
    with ``email:a@x.com`` never collapses into "Alice Smith" with no anchor.
    """
    normalized_type = (node_type or "OTHER").strip().upper()
    normalized_name = normalize_name(name)
    if not normalized_name:
        normalized_name = "unnamed"

    if anchor:
        kind, _, value = anchor.partition(":")
        candidate = f"{normalized_type}|anchor:{kind}|{normalize_name(value)}"
    else:
        candidate = f"{normalized_type}|name|{normalized_name}"

    if len(candidate) <= MAX_IDENTITY_KEY_CHARS:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:24]
    return f"{candidate[: MAX_IDENTITY_KEY_CHARS - 25]}#{digest}"


def relation_key(relation_type: str) -> str:
    """Normalize a relation label into a stable key (``REPORTS_TO``)."""
    slug = _RELATION_SLUG.sub("_", (relation_type or "").strip().lower()).strip("_")
    slug = slug.upper()
    return (slug or "RELATED_TO")[:MAX_RELATION_CHARS]


def display_relation(key: str) -> str:
    """Human-readable form used in generated summaries (``reports to``)."""
    return (key or "RELATED_TO").replace("_", " ").lower()


def clamp_confidence(value: object, *, default: float = 0.0) -> float:
    """Coerce an untrusted confidence value into ``[0, 1]``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if number != number:  # NaN
        return default
    return max(0.0, min(1.0, number))


def truncate(value: str, limit: int) -> str:
    """Bound a string without splitting a surrogate pair or trailing space."""
    if len(value) <= limit:
        return value
    return value[:limit].rstrip()
