"""Traversal is pure logic, so these tests need no database."""

from uuid import uuid4

from app.services.graph.traversal import (
    adjacency,
    bounded_traversal,
    order_by_proximity,
    shortest_path,
)
from app.services.graph.types import VisibleEdge


def edge(source, target, relation="RELATED_TO"):
    return VisibleEdge(
        edge_id=uuid4(),
        source_node_id=source,
        target_node_id=target,
        relation_type=relation,
        confidence=1.0,
    )


def test_adjacency_is_undirected():
    a, b = uuid4(), uuid4()
    graph = adjacency([edge(a, b)])
    assert [node for node, _ in graph[a]] == [b]
    assert [node for node, _ in graph[b]] == [a]


def test_traversal_respects_depth():
    a, b, c, d = (uuid4() for _ in range(4))
    edges = [edge(a, b), edge(b, c), edge(c, d)]

    shallow = bounded_traversal([a], edges, max_depth=1, max_nodes=10, max_edges=10)
    assert set(shallow.node_ids) == {a, b}
    assert shallow.depth_reached == 1

    deep = bounded_traversal([a], edges, max_depth=3, max_nodes=10, max_edges=10)
    assert set(deep.node_ids) == {a, b, c, d}


def test_traversal_reports_truncation_at_node_budget():
    a, b, c = (uuid4() for _ in range(3))
    edges = [edge(a, b), edge(b, c)]
    result = bounded_traversal([a], edges, max_depth=3, max_nodes=2, max_edges=10)
    assert result.truncated is True
    assert len(result.node_ids) <= 2


def test_traversal_reports_truncation_at_edge_budget():
    a, b, c = (uuid4() for _ in range(3))
    edges = [edge(a, b), edge(a, c)]
    result = bounded_traversal([a], edges, max_depth=2, max_nodes=10, max_edges=1)
    assert result.truncated is True
    assert len(result.edge_ids) == 1


def test_empty_seeds_return_empty_graph():
    result = bounded_traversal([], [edge(uuid4(), uuid4())], max_depth=2, max_nodes=5, max_edges=5)
    assert result.node_ids == ()
    assert result.edge_ids == ()


def test_shortest_path_finds_bfs_route():
    a, b, c, d = (uuid4() for _ in range(4))
    graph = adjacency([edge(a, b), edge(b, c), edge(a, d)])
    assert shortest_path(graph, a, c, max_depth=3, max_nodes=10) == [a, b, c]
    assert shortest_path(graph, c, d, max_depth=1, max_nodes=10) is None


def test_order_by_proximity_puts_seeds_first():
    a, b, c = (uuid4() for _ in range(3))
    edges = [edge(a, b), edge(b, c)]
    result = bounded_traversal([a], edges, max_depth=2, max_nodes=10, max_edges=10)
    ordered = order_by_proximity(result.node_ids, result.parents, seeds=[a])
    assert ordered[0] == a
