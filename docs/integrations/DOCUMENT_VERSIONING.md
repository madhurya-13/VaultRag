# Document Versioning

Synchronized documents change. VaultRAG keeps every ingested revision as an
immutable stored artifact and makes rollback a first-class, audited operation.

## Model

`document_versions` stores metadata for one revision:

| Column | Meaning |
| --- | --- |
| `version_number` | Monotonic per document, starting at 1 |
| `content_checksum` | SHA-256 computed by VaultRAG from the bytes it stored |
| `file_size` | Stored size |
| `storage_path` | Generated private storage key for this revision |
| `external_version_id` | Provider revision id when one exists |
| `source_modified_at` | Provider modification time |
| `ingestion_status` | `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED` |
| `is_current` | Exactly one current version per document (partial unique index) |

Chunks, embeddings, graph evidence and retrieval predicates stay attached to the
live `documents` row. A version therefore never needs a `version_id` predicate in
any retrieval, authorization or graph query, and no RLS policy changes.

## Lifecycle

1. A change is discovered (full listing or provider delta).
2. The payload is downloaded and hashed locally.
3. If the digest equals the stored checksum and the live document is
   `COMPLETED`, the run records `last_seen_at` and creates **no** version.
4. Otherwise the previous live chunks are deleted, a new storage key is generated,
   the bytes are stored, and a `PENDING` version row is written together with the
   updated document metadata.
5. The existing ingestion pipeline runs. On success the version becomes
   `COMPLETED` and current, and the previous current row is deactivated. On
   failure the version is `FAILED` and the document is marked FAILED by ingress.

Because the digest check happens before ingestion, re-synchronizing an unchanged
source is idempotent and cheap.

## Rollback

`POST /documents/{id}/versions/{vid}/restore` (ADMIN + document
`MANAGE_PERMISSIONS`):

1. only a `COMPLETED` version can be restored;
2. live chunks are deleted;
3. the document points at the version's stored bytes and checksum;
4. the version becomes current;
5. the normal pipeline re-ingests those bytes.

The version's own file is never moved or deleted, so the same rollback can be
repeated. A failed re-ingestion leaves an explicit FAILED document rather than a
mixed index. Restores are audited as `DOCUMENT_VERSION_RESTORE`.

## Retention

Versions are retained. The runtime role has no DELETE grant on
`document_versions`, so an application bug cannot erase history. If storage
growth becomes a concern, add an operator-owned retention job that archives old
version bytes *and* rows together; do not delete only the file.

## Interaction with the knowledge graph

Re-ingestion invalidates a document's graph evidence and queues fresh extraction,
so graph triples derived from an older revision cannot be served as current
evidence. An archived document's evidence is invalidated and its document row is
inactive, which removes it from retrieval.
