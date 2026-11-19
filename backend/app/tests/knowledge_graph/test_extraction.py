"""Extraction and validation tests.

The validation gate is the security boundary for LLM output, so these tests
assert rejection as much as acceptance.
"""

import pytest

from app.services.graph.extraction import (
    ExtractionValidationError,
    extract_chunk,
    extract_with_rules,
    validate_extraction_payload,
)

CHUNK = (
    "Acme Inc owns the Data Retention Policy. "
    "Alice Smith reports to Bob Jones. "
    "The Security Team manages Vault-2024."
)


def test_rules_extract_entities_and_relations():
    result = extract_with_rules(CHUNK)
    names = {entity.name for entity in result.entities}
    assert "Acme Inc" in names
    assert "Data Retention Policy" in names
    assert any(
        relation.relation_type == "OWNS" for relation in result.relations
    )


def test_rules_never_raise_on_empty_text():
    assert extract_with_rules("").is_empty()
    assert extract_with_rules("   ").is_empty()


def test_extract_chunk_discards_unsupported_claims():
    payload = {
        "entities": [
            {"name": "Acme Inc", "type": "ORGANIZATION", "quote": "Acme Inc", "confidence": 0.9}
        ],
        "relations": [],
    }
    result = validate_extraction_payload(
        payload, chunk_text=CHUNK, max_quote_chars=600, method="RULES"
    )
    assert len(result.entities) == 1

    with pytest.raises(ExtractionValidationError):
        validate_extraction_payload(
            {
                "entities": [
                    {
                        "name": "Globex",
                        "type": "ORGANIZATION",
                        "quote": "Globex is not in the text",
                        "confidence": 0.9,
                    }
                ],
                "relations": [],
            },
            chunk_text=CHUNK,
            max_quote_chars=600,
            method="RULES",
        )


def test_validation_rejects_name_outside_its_quote():
    with pytest.raises(ExtractionValidationError):
        validate_extraction_payload(
            {
                "entities": [
                    {
                        "name": "Globex",
                        "type": "ORGANIZATION",
                        "quote": "Acme Inc",
                        "confidence": 0.9,
                    }
                ],
                "relations": [],
            },
            chunk_text=CHUNK,
            max_quote_chars=600,
            method="RULES",
        )


def test_validation_rejects_relation_to_undeclared_entity():
    with pytest.raises(ExtractionValidationError):
        validate_extraction_payload(
            {
                "entities": [
                    {
                        "name": "Acme Inc",
                        "type": "ORGANIZATION",
                        "quote": "Acme Inc",
                        "confidence": 0.9,
                    }
                ],
                "relations": [
                    {
                        "source": "Acme Inc",
                        "target": "Globex",
                        "relation": "OWNS",
                        "quote": "Acme Inc owns the Data Retention Policy",
                        "confidence": 0.9,
                    }
                ],
            },
            chunk_text=CHUNK,
            max_quote_chars=600,
            method="RULES",
        )


def test_validation_rejects_oversized_batches():
    payload = {
        "entities": [
            {"name": f"Entity {i}", "type": "OTHER", "quote": "Acme Inc", "confidence": 0.5}
            for i in range(20)
        ],
        "relations": [],
    }
    with pytest.raises(ExtractionValidationError):
        validate_extraction_payload(
            payload, chunk_text=CHUNK, max_quote_chars=600, method="RULES"
        )


def test_extract_chunk_degrades_when_quote_too_long():
    result = extract_chunk(
        CHUNK,
        method="RULES",
        spacy_model="en_core_web_sm",
        min_confidence=0.9,
        max_quote_chars=20,
        max_entities=5,
        max_relations=5,
    )
    # Every rule quote is longer than the allowed quote length, so the batch is
    # discarded instead of being stored unquotable.
    assert result.is_empty()
