"""Bounded traversal over an already-authorized edge set.

Pure logic: no database. That is deliberate — the depth/width limits and the
determinism of the result are properties of the algorithm, so they are unit
tested without fixtures.

Traversal is bidirectional (an edge is followed in either direction) because a
caller asking "what touches this policy" cares about incoming as well as outgoing
relationships. Every hop is bounded: depth, node count and edge count each have a
hard cap, and hitting any cap sets ``truncated`` instead of silently returning a
different answer shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.services.graph.types import VisibleEdge


@dataclass(frozen=True, slots=True)
class TraversalResult:
    node_ids: tuple[UUID, ...]
    edge_ids: tuple[UUID, ...]
    parents: dict[UUID, UUID | None]
    truncated: bool
    depth_reached: int


def adjacency(edges: list[VisibleEdge]) -> dict[UUID, list[tuple[UUID, VisibleEdge]]]:
    """Undirected adjacency list, ordered deterministically by edge id."""
    graph: dict[UUID, list[tuple[UUID, VisibleEdge]]] = {}
    for edge in sorted(edges, key=lambda item: str(item.edge_id)):
        graph.setdefault(edge.source_node_id, []).append((edge.target_node_id, edge))
        graph.setdefault(edge.target_node_id, []).append((edge.source_node_id, edge))
    return graph


def shortest_path(
    graph: dict[UUID, list[tuple[UUID, VisibleEdge]]],
    source: UUID,
    target: UUID,
    *,
    max_depth: int,
    max_nodes: int,
) -> list[UUID] | None:
    """Breadth-first path over visible edges, or ``None`` when unreachable."""
    if source == target:
        return [source]
    if max_depth < 1:
        return None

    visited: set[UUID] = {source}
    queue: list[tuple[UUID, list[UUID]]] = [(source, [source])]
    while queue:
        node, path = queue.pop(0)
        if len(path) - 1 >= max_depth:
            continue
        for neighbour, _edge in graph.get(node, ()):
            if neighbour in visited:
                continue
            next_path = path + [neighbour]
            if neighbour == target:
                return next_path
            visited.add(neighbour)
            if len(visited) >= max_nodes:
                return None
            queue.append((neighbour, next_path))
    return None


def bounded_traversal(
    seeds: list[UUID],
    edges: list[VisibleEdge],
    *,
    max_depth: int,
    max_nodes: int,
    max_edges: int,
) -> TraversalResult:
    """Expand ``seeds`` up to ``max_depth`` hops within node/edge budgets."""
    if not seeds or max_depth < 1:
        return TraversalResult(
            node_ids=tuple(dict.fromkeys(seeds)),
            edge_ids=(),
            parents={seed: None for seed in seeds},
            truncated=False,
            depth_reached=0,
        )

    graph = adjacency(edges)
    ordered_seeds = list(dict.fromkeys(seeds))[:max_nodes]
    parents: dict[UUID, UUID | None] = {seed: None for seed in ordered_seeds}
    accepted_edges: dict[UUID, VisibleEdge] = {}
    frontier = list(ordered_seeds)
    truncated = False
    depth_reached = 0

    for depth in range(1, max_depth + 1):
        next_frontier: list[UUID] = []
        for node in frontier:
            for neighbour, edge in graph.get(node, ()):
                if len(accepted_edges) >= max_edges:
                    truncated = True
                    break
                if edge.edge_id not in accepted_edges:
                    accepted_edges[edge.edge_id] = edge
                if neighbour in parents:
                    continue
                if len(parents) >= max_nodes:
                    truncated = True
                    continue
                parents[neighbour] = node
                next_frontier.append(neighbour)
            if len(accepted_edges) >= max_edges and len(parents) >= max_nodes:
                break
        if next_frontier:
            depth_reached = depth
        if not next_frontier or truncated and len(parents) >= max_nodes:
            break
        frontier = next_frontier

    return TraversalResult(
        node_ids=tuple(parents.keys()),
        edge_ids=tuple(accepted_edges.keys()),
        parents=parents,
        truncated=truncated,
        depth_reached=depth_reached,
    )


def order_by_proximity(
    node_ids: tuple[UUID, ...],
    parents: dict[UUID, UUID | None],
    *,
    seeds: list[UUID],
) -> list[UUID]:
    """Seeds first, then breadth-first distance, then insertion order."""
    depth: dict[UUID, int] = {}
    for node_id in node_ids:
        hops = 0
        cursor = node_id
        seen: set[UUID] = set()
        while parents.get(cursor) is not None and cursor not in seen:
            seen.add(cursor)
            cursor = parents[cursor]  # type: ignore[assignment]
            hops += 1
        depth[node_id] = hops

    seed_rank = {node_id: index for index, node_id in enumerate(seeds)}
    return sorted(
        node_ids,
        key=lambda node_id: (
            depth.get(node_id, 0),
            seed_rank.get(node_id, len(seed_rank)),
            str(node_id),
        ),
    )
