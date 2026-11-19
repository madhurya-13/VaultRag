"""Pure unit tests for normalization: no database, no fixtures."""

from app.services.graph.normalization import (
    clamp_confidence,
    display_relation,
    identity_key,
    infer_anchor,
    name_in_quote,
    normalize_name,
    normalize_quote,
    quote_supported,
    relation_key,
    truncate,
)


def test_normalize_name_collapses_punctuation_and_case():
    assert normalize_name("Acme, Inc.") == "acme inc"
    assert normalize_name("  ACME   Inc  ") == "acme inc"
    assert normalize_name("Café Ltd") == "cafe ltd"


def test_normalize_name_is_stable_for_unicode_diacritics():
    assert normalize_name("José") == normalize_name("Jose")


def test_identity_key_separates_homonyms_without_anchor():
    plain = identity_key("PERSON", "Alice Smith")
    anchored = identity_key("PERSON", "Alice Smith", "email:alice@example.com")
    assert plain != anchored
    assert plain.startswith("PERSON|name|")
    assert anchored.startswith("PERSON|anchor:email|")


def test_identity_key_is_bounded():
    key = identity_key("ORGANIZATION", "x" * 400)
    assert len(key) <= 160


def test_infer_anchor_detects_email_and_employee_id():
    assert infer_anchor("Contact alice@example.com today") == "email:alice@example.com"
    assert infer_anchor("Employee ID: EMP-1042") == "employee:emp-1042"
    assert infer_anchor("No identifier here") is None


def test_infer_anchor_ignores_plain_prose():
    # "employee" followed by an ordinary word must not become an anchor.
    assert infer_anchor("Every employee must complete training") is None


def test_abbreviation_dot_does_not_defeat_identity():
    assert normalize_name("Acme, Inc.") == normalize_name("Acme Inc")
    assert identity_key("ORGANIZATION", "Acme, Inc.") == identity_key(
        "ORGANIZATION", "Acme Inc"
    )
    # Decimals keep their separator.
    assert normalize_name("Version 1.2") == "version 1.2"


def test_quote_supported_is_case_and_whitespace_insensitive():
    assert quote_supported("Acme   Inc owns", "acme inc\nowns the policy")
    assert not quote_supported("Acme owns everything", "Acme Inc owns the policy")


def test_name_in_quote_requires_verbatim_name():
    assert name_in_quote("Acme Inc", "Acme Inc owns the policy")
    assert not name_in_quote("Acme Corp", "Acme Inc owns the policy")


def test_relation_key_normalizes_labels():
    assert relation_key("reports to") == "REPORTS_TO"
    assert relation_key("") == "RELATED_TO"
    assert display_relation("REPORTS_TO") == "reports to"


def test_clamp_confidence_rejects_bool_and_nan():
    assert clamp_confidence(True) == 0.0
    assert clamp_confidence(float("nan")) == 0.0
    assert clamp_confidence(2.5) == 1.0
    assert clamp_confidence(-1) == 0.0
    assert clamp_confidence(0.42) == 0.42


def test_truncate_respects_limit():
    assert truncate("abcdef", 3) == "abc"
    assert truncate("abc", 10) == "abc"


def test_normalize_quote_collapses_whitespace():
    assert normalize_quote(" A\n\tB ") == "a b"
