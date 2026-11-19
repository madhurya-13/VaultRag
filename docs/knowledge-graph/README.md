# Knowledge Graph Intelligence Engine (Milestone 8)

VaultRAG's knowledge graph turns the documents a tenant already has into a
network of entities and typed relationships, then answers questions against that
network **without ever widening document access**.

The governing invariant:

> No entity, relationship, source evidence, or graph-derived information from an
> unauthorized document may be exposed to a user or used to generate that user's
> answer.

## What it adds

| Layer | Location |
| --- | --- |
| Extraction (rules → spaCy → optional local LLM) | `backend/app/services/graph/extraction.py` |
| Entity resolution and merge safety | `backend/app/services/graph/entity_resolution.py` |
| Evidence-gated visibility | `backend/app/services/graph/authorization.py` |
| Bounded retrieval and findings | `backend/app/services/graph/retrieval.py` |
| Conflict detection | `backend/app/services/graph/conflicts.py` |
| Admin review / merge / split | `backend/app/services/graph/governance.py` |
| Durable jobs and worker | `backend/app/services/graph/jobs.py`, `backend/scripts/graph_worker.py` |
| HTTP API | `backend/app/routes/knowledge_graph.py` |
| Storage | `backend/alembic/versions/c8f1d2e3a4b5_milestone8_knowledge_graph.py` |
| UI | `frontend/src/pages/graph-page.tsx` |

## Storage

Nine PostgreSQL tables, all tenant-scoped and protected by forced row-level
security:

- `knowledge_nodes` — canonical entities, one identity per `(tenant, type, identity_key)`
- `knowledge_edges` — unique `(tenant, source, target, relation_key)` triples
- `knowledge_evidence` — the **only** authorization surface of the graph
- `entity_aliases` — alternative surface forms
- `knowledge_extraction_jobs` — one active job per document
- `knowledge_entity_reviews` — ambiguous candidates awaiting a human
- `knowledge_entity_merges` — immutable merge/split audit rows
- `knowledge_conflicts` — contradictions between authorized documents
- `knowledge_query_analytics` — counts and latencies only

No graph database is used. Apache AGE was evaluated and rejected: the access
pattern is a small, bounded, per-request subgraph, which composite-indexed
relational tables answer without a second engine, a second RLS model or a second
backup story.

## Why relational, not AGE/Neo4j

1. **Authorization is the hard part, not traversal.** Visibility is a join
   against evidence and an ABAC decision per document. That already lives in
   PostgreSQL.
2. **RLS.** Tenant isolation is a database rule today. A property graph would
   need a parallel isolation story.
3. **Bounded work.** Queries cap depth (default 2, max 3), nodes (200), edges
   (400) and evidence rows (100). A recursive CTE plus application-side
   filtering is sufficient at these sizes.

If a future milestone needs unbounded multi-hop analytics, AGE can be added as a
derived read model — the evidence tables would remain the source of truth.

## Retrieval modes

`search_mode` on `POST /rag/ask` now accepts:

- `graph` — only chunks that support visible graph evidence
- `graph_hybrid` — graph chunks fused with semantic and keyword candidates (RRF)

`POST /knowledge-graph/query` exposes the subgraph itself (nodes, edges,
findings, conflicts) for exploration.

## Merge safety

Two people called "Alice Smith" are **two nodes** unless a shared anchor (email
or employee ID) or an administrator says otherwise. Fuzzy similarity only ever
creates a `PENDING` review for non-PERSON types above the review threshold, and
auto-merges only above the higher auto-merge threshold. PERSON nodes without an
anchor are never auto-merged.

See [architecture.md](architecture.md) for the request flow and
[operations.md](operations.md) for running the worker and troubleshooting.
