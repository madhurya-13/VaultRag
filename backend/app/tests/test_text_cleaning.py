"""Unit tests for conservative PDF text cleaning."""

from app.services.text_cleaner import clean_text, has_meaningful_text


def test_clean_text_normalizes_whitespace_and_line_endings():
    raw = "  Policy\u00a0number 42\r\n\tOwner:  Finance   \r\n\r\n\r\n\r\nApproved.  "
    cleaned = clean_text(raw)

    assert cleaned == "Policy number 42\nOwner: Finance\n\nApproved."
    assert "\r" not in cleaned
    assert "\u00a0" not in cleaned
    assert "\u200b" not in clean_text("secret\u200bvalue")


def test_clean_text_preserves_punctuation_numbers_and_technical_terms():
    raw = "Use OAuth2.0; retry 3x at 09:30. Amount = $1,250.50."
    assert clean_text(raw) == raw


def test_clean_text_collapses_excess_blank_lines_but_keeps_paragraphs():
    cleaned = clean_text("First paragraph.\n\n\n\nSecond paragraph.")
    assert cleaned == "First paragraph.\n\nSecond paragraph."


def test_clean_text_handles_empty_and_whitespace_only_values():
    assert clean_text("") == ""
    assert clean_text(" \t\r\n ") == ""


def test_has_meaningful_text():
    assert has_meaningful_text("x")
    assert not has_meaningful_text("")
    assert not has_meaningful_text(" \n\t ")
