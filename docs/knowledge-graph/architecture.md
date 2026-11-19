# Knowledge Graph Architecture

## Ingestion → extraction

```
document COMPLETED ──> enqueue_graph_job (savepoint, failure swallowed)
                              │
                              ▼
                     knowledge_extraction_jobs (PENDING)
                              │  claim: FOR UPDATE SKIP LOCKED
                              ▼
        invalidate this document's valid evidence
                              │
        for each chunk: extract → validate → resolve → edge → evidence
                              │
              refresh_graph_state (counts, archive empty rows)
                              │
                     detect + record conflicts (touched edges)
                              │
                              ▼
                    job COMPLETED / PENDING / FAILED
```

Graph extraction is **separate from ingestion**:

- its own job table and status domain (`PENDING`, `PROCESSING`, `COMPLETED`,
  `FAILED`, `SKIPPED`);
- the document stays `COMPLETED` and fully retrievable while extraction is
  pending or failing;
- reprocessing is idempotent: valid evidence for the document is invalidated
  first, then everything is re-derived;
- a failure is retried up to `KNOWLEDGE_GRAPH_JOB_MAX_ATTEMPTS`, and a job whose
  `started_at` is older than `KNOWLEDGE_GRAPH_JOB_STALE_SECONDS` is reclaimed.

## Extraction chain

1. **Rules** — deterministic regexes for policies, organizations, systems,
   roles, dates, departments and explicit relation phrases. Always on.
2. **spaCy** — optional; a missing package or model logs and degrades to rules.
   It never becomes a startup dependency.
3. **Ollama** — optional and off by default. Output is untrusted.

Every path funnels through `validate_extraction_payload`, which enforces:

- object payload with list-valued `entities` and `relations`;
- bounded counts, lengths, types and confidence in `[0, 1]`;
- every quote is a whitespace/case-normalized substring of the source chunk;
- every entity name appears inside its own quote;
- every relation endpoint is a declared entity and both names appear in the
  relation quote;
- no self-relations.

A batch that fails validation is discarded whole. A discarded chunk stores no
claims rather than partial claims.

## Read path

```
POST /knowledge-graph/query
        │
        ├── load roles + ABAC attributes once        (per request)
        ├── seed: name/alias match                    (candidate only)
        ├── authorize node evidence                   (document READ)
        ├── hop 1..depth:
        │     edges touching frontier
        │     authorize EDGE evidence
        │     authorize endpoint node evidence
        │     keep edge only if both endpoints visible
        ├── build findings from surviving edges
        └── candidate chunks from surviving evidence chunk ids
```

Rules that fall out of this ordering:

- A node is invisible without node evidence. Not its name, not its count, not
  its alias.
- An edge is invisible without *edge* evidence. Two visible nodes never imply a
  visible relationship.
- Descriptions and mention counts come only from authorized evidence.
- Chunk text is fetched only after authorization, using the existing
  `fetch_chunk_texts` helper; the graph layer never selects text itself.
- Permission decisions are cached **only** for the duration of one request
  (`GraphAuthorizationContext.document_decisions`). Nothing is cached across
  requests, so revocation, expiry, archival and ABAC changes apply immediately.
- Documents that are not `COMPLETED` are denied: a document mid-reingestion may
  have its chunks replaced under the query.
- Every budget is hard: depth, nodes, edges and evidence rows per subject set.
  Hitting a budget sets `truncated`; it never silently changes the answer shape.

## Deletion and invalidation

No runtime DELETE is issued against graph tables. Instead:

- archiving or reprocessing a document invalidates its evidence
  (`is_valid = false`, `invalidated_at` set);
- `refresh_edge_counts` / `refresh_node_state` recompute counts and archive rows
  that no longer have valid evidence;
- archived rows stay for audit and are revived if the document is re-ingested.

Because visibility requires a *valid, currently authorized* evidence row,
invalidation is immediately effective even before the aggregates are recomputed.

## Admin governance

`MANAGE_KNOWLEDGE_GRAPH` is ADMIN-only.

- **Merge** moves aliases, re-parents node evidence, re-points edges and
  deduplicates triples, archives the source node, then writes a
  `knowledge_entity_merges` row.
- **Split** creates a node for one alias and moves only evidence whose quote
  actually contains that alias.
- **Review decision** approves (merging the suggestion) or rejects a pending
  review, recording the reviewer, timestamp and note.

All three recompute derived state and are audited through the security audit log.
