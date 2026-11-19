"""Seed tokenization and finding construction (no database)."""

from uuid import uuid4

from app.services.graph.retrieval import MAX_QUERY_TOKENS, build_findings, query_tokens
from app.services.graph.types import (
    GraphSnapshot,
    VisibleEdge,
    VisibleEvidence,
    VisibleNode,
)


def test_query_tokens_drop_stopwords_and_short_tokens():
    tokens = query_tokens("Who reports to the Head of Security?")
    assert "who" not in tokens
    assert "the" not in tokens
    assert "reports" in tokens
    assert "security" in tokens


def test_query_tokens_are_deduplicated_and_bounded():
    tokens = query_tokens("policy " * 50)
    assert tokens == ["policy"]
    assert len(query_tokens(" ".join(f"token{i}" for i in range(40)))) <= MAX_QUERY_TOKENS


def test_query_tokens_handle_empty_input():
    assert query_tokens("") == []


def test_build_findings_uses_edge_evidence_and_node_names():
    source_id, target_id = uuid4(), uuid4()
    document_id, chunk_id = uuid4(), uuid4()
    evidence = VisibleEvidence(
        evidence_id=uuid4(),
        document_id=document_id,
        document_name="HR Handbook",
        chunk_id=chunk_id,
        page_start=3,
        page_end=4,
        quote="Alice reports to Bob.",
        confidence=0.8,
    )
    snapshot = GraphSnapshot(
        nodes=(
            VisibleNode(source_id, "PERSON", "Alice", None, 0.9, 1, (evidence,)),
            VisibleNode(target_id, "PERSON", "Bob", None, 0.9, 1, (evidence,)),
        ),
        edges=(
            VisibleEdge(
                uuid4(), source_id, target_id, "REPORTS_TO", 0.7, (evidence,)
            ),
        ),
        seeds=(source_id,),
    )

    findings = build_findings(snapshot, limit=5)
    assert len(findings) == 1
    finding = findings[0]
    assert (finding.subject, finding.relation, finding.object) == (
        "Alice",
        "reports to",
        "Bob",
    )
    assert finding.document_id == document_id
    assert finding.chunk_id == chunk_id
    assert finding.confidence == 0.7


def test_build_findings_respects_limit_and_skips_edgeless_graphs():
    snapshot = GraphSnapshot()
    assert build_findings(snapshot, limit=5) == ()
