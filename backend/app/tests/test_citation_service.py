"""Citation validation against backend-generated source IDs."""

from uuid import uuid4

from app.services.citation_service import validate_citations
from app.services.context_builder import ContextSource


def _source(source_id: str, name: str = "Policy.pdf"):
    return ContextSource(
        source_id=source_id,
        document_id=uuid4(),
        document_name=name,
        chunk_id=uuid4(),
        page_start=2,
        page_end=2,
        text="policy text",
        similarity=0.9,
    )


def test_valid_citation_preserves_backend_metadata():
    first = _source("S1", "HR_Policy.pdf")
    second = _source("S2", "HR_Handbook.pdf")

    result = validate_citations("Annual leave is 18 days [S1]. See also [S2].", [first, second])

    assert result.has_valid_citation is True
    assert result.answer == "Annual leave is 18 days [S1]. See also [S2]."
    assert result.sources == (first, second)
    assert result.invalid_source_ids == ()


def test_fabricated_citation_is_removed_and_not_exposed():
    source = _source("S1")

    result = validate_citations("A confident but uncited claim [S99].", [source])

    assert result.has_valid_citation is False
    assert result.sources == ()
    assert "S99" not in result.answer
    assert result.invalid_source_ids == ("S99",)


def test_mixed_valid_and_invalid_citations_keep_only_valid_source():
    source = _source("S1")

    result = validate_citations("Policy fact [S1] and invented fact [S42].", [source])

    assert result.has_valid_citation is True
    assert result.sources == (source,)
    assert result.answer == "Policy fact [S1] and invented fact."
    assert result.invalid_source_ids == ("S42",)


def test_answer_without_any_citation_is_not_treated_as_verifiable():
    result = validate_citations("An uncited answer.", [_source("S1")])

    assert result.has_valid_citation is False
    assert result.sources == ()
