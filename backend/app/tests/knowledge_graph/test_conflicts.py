"""Conflict detection tests: authorized inputs only, no database."""

from uuid import uuid4

from app.services.graph.conflicts import detect_functional_conflicts
from app.services.graph.types import VisibleEdge, VisibleEvidence, VisibleNode


def node(name, node_type="ORGANIZATION"):
    node_id = uuid4()
    return node_id, VisibleNode(
        node_id=node_id,
        node_type=node_type,
        name=name,
        description=None,
        confidence=0.9,
        mention_count=1,
    )


def evidence(document_id, document_name, quote="quote"):
    return VisibleEvidence(
        evidence_id=uuid4(),
        document_id=document_id,
        document_name=document_name,
        chunk_id=uuid4(),
        page_start=1,
        page_end=1,
        quote=quote,
        confidence=0.9,
    )


def build_edges(source, targets, relation="MANAGED_BY", documents=None):
    edges = []
    for index, target in enumerate(targets):
        documents = documents or [uuid4() for _ in targets]
        edges.append(
            VisibleEdge(
                edge_id=uuid4(),
                source_node_id=source,
                target_node_id=target,
                relation_type=relation,
                confidence=0.9,
                evidence=(evidence(documents[index], f"Doc {index}"),),
            )
        )
    return edges


def test_conflict_detected_across_two_documents():
    subject_id, subject = node("Platform")
    first_id, first = node("Alice")
    second_id, second = node("Bob")
    nodes = {subject_id: subject, first_id: first, second_id: second}

    conflicts = detect_functional_conflicts(
        build_edges(subject_id, [first_id, second_id]), nodes
    )
    assert len(conflicts) == 1
    assert {value.value for value in conflicts[0].values} == {"Alice", "Bob"}


def test_single_target_is_not_a_conflict():
    subject_id, subject = node("Platform")
    first_id, first = node("Alice")
    nodes = {subject_id: subject, first_id: first}
    assert detect_functional_conflicts(build_edges(subject_id, [first_id]), nodes) == []


def test_same_document_cannot_contradict_itself():
    subject_id, subject = node("Platform")
    first_id, first = node("Alice")
    second_id, second = node("Bob")
    nodes = {subject_id: subject, first_id: first, second_id: second}
    document = uuid4()

    conflicts = detect_functional_conflicts(
        build_edges(
            subject_id,
            [first_id, second_id],
            documents=[document, document],
        ),
        nodes,
    )
    assert conflicts == []


def test_non_functional_relation_is_ignored():
    subject_id, subject = node("Platform")
    first_id, first = node("Alice")
    second_id, second = node("Bob")
    nodes = {subject_id: subject, first_id: first, second_id: second}

    conflicts = detect_functional_conflicts(
        build_edges(subject_id, [first_id, second_id], relation="RELATED_TO"),
        nodes,
    )
    assert conflicts == []


def test_edges_without_evidence_are_ignored():
    subject_id, subject = node("Platform")
    first_id, first = node("Alice")
    second_id, second = node("Bob")
    nodes = {subject_id: subject, first_id: first, second_id: second}

    edges = build_edges(subject_id, [first_id, second_id])
    bare = [
        VisibleEdge(
            edge_id=item.edge_id,
            source_node_id=item.source_node_id,
            target_node_id=item.target_node_id,
            relation_type=item.relation_type,
            confidence=item.confidence,
            evidence=(),
        )
        for item in edges
    ]
    assert detect_functional_conflicts(bare, nodes) == []
