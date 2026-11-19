"""Milestone 8: enterprise knowledge graph intelligence engine.

Revision ID: c8f1d2e3a4b5
Revises: b7d4c9e2a1f0
Create Date: 2026-11-17

What this migration adds
------------------------
1. ``document_chunks`` gains ``UNIQUE (tenant_id, document_id, chunk_id)`` so
   ``knowledge_evidence`` can use a fully tenant-scoped composite foreign key to
   the chunk that proves the claim. Without it a graph assertion could reference
   another tenant's chunk id.
2. Nine graph tables. Every one carries ``tenant_id``, forced row level security
   and the same ``security.current_tenant_id()`` policy as the rest of the
   schema, so the graph inherits the existing tenant boundary instead of
   introducing a second authorization path.
3. ``retrieval_analytics`` accepts the two new search modes ``graph`` and
   ``graph_hybrid``.

Storage decision
----------------
The graph is stored relationally, not in Apache AGE or an external graph
database. Reasons: AGE is absent from ``pgvector/pgvector:pg16`` (it needs a
custom image and superuser bootstrap), AGE graphs do not participate in the
existing per-table RLS policies, and the traversal depth this product needs is
two to three hops over a bounded candidate set. A recursive CTE over indexed
adjacency tables is both simpler to audit and cheaper to operate here. See
``docs/knowledge-graph/architecture.md`` for the full justification and the
conditions under which that decision should be revisited.

Deletion policy
---------------
There are no DELETE grants for the runtime role. Evidence is invalidated
(``is_valid = false`` + ``invalidated_at``) and nodes/edges are archived, so a
document archive or re-process removes claims from answers without destroying
the audit trail. Rows disappear only through the ``ON DELETE CASCADE`` of a
tenant or document hard delete performed by the migrator role.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8f1d2e3a4b5"
down_revision: Union[str, None] = "b7d4c9e2a1f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "vaultrag_app"

TENANT_TABLES = (
    "knowledge_nodes",
    "knowledge_edges",
    "knowledge_evidence",
    "entity_aliases",
    "knowledge_extraction_jobs",
    "knowledge_entity_reviews",
    "knowledge_entity_merges",
    "knowledge_conflicts",
    "knowledge_query_analytics",
)

APPEND_ONLY_TABLES = (
    "knowledge_query_analytics",
    "knowledge_entity_merges",
)


def _enable_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            AS PERMISSIVE
            FOR ALL
            TO PUBLIC
            USING (
                security.current_tenant_id() IS NOT NULL
                AND tenant_id = security.current_tenant_id()
            )
            WITH CHECK (
                security.current_tenant_id() IS NOT NULL
                AND tenant_id = security.current_tenant_id()
            )
            """
        )
    )


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Composite chunk key for evidence foreign keys
    # ------------------------------------------------------------------
    op.create_unique_constraint(
        "uq_document_chunks_tenant_document_chunk",
        "document_chunks",
        ["tenant_id", "document_id", "chunk_id"],
    )

    # ------------------------------------------------------------------
    # 2. Graph nodes
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_nodes",
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("node_type", sa.String(length=32), nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("identity_key", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "properties",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "extraction_confidence",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "mention_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "is_archived",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "node_type IN ('PERSON', 'ORGANIZATION', 'LOCATION', 'POLICY', "
            "'PROCESS', 'SYSTEM', 'PRODUCT', 'DATE', 'ROLE', 'DEPARTMENT', "
            "'OTHER')",
            name="ck_knowledge_nodes_type",
        ),
        sa.CheckConstraint(
            "length(trim(canonical_name)) > 0 AND length(canonical_name) <= 255",
            name="ck_knowledge_nodes_canonical_name",
        ),
        sa.CheckConstraint(
            "length(trim(display_name)) > 0 AND length(display_name) <= 255",
            name="ck_knowledge_nodes_display_name",
        ),
        sa.CheckConstraint(
            "length(trim(normalized_name)) > 0",
            name="ck_knowledge_nodes_normalized",
        ),
        sa.CheckConstraint(
            "length(trim(identity_key)) > 0", name="ck_knowledge_nodes_identity"
        ),
        sa.CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="ck_knowledge_nodes_confidence",
        ),
        sa.CheckConstraint(
            "mention_count >= 0", name="ck_knowledge_nodes_mention_count"
        ),
        sa.CheckConstraint(
            "description IS NULL OR length(description) <= 4000",
            name="ck_knowledge_nodes_description",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_knowledge_nodes_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("node_id", name=op.f("pk_knowledge_nodes")),
        sa.UniqueConstraint(
            "tenant_id", "node_id", name="uq_knowledge_nodes_tenant_node"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "node_type",
            "identity_key",
            name="uq_knowledge_nodes_identity",
        ),
    )
    op.create_index(
        "ix_knowledge_nodes_tenant_type",
        "knowledge_nodes",
        ["tenant_id", "node_type", "is_archived"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_nodes_tenant_normalized",
        "knowledge_nodes",
        ["tenant_id", "normalized_name"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_nodes_tenant_updated",
        "knowledge_nodes",
        ["tenant_id", "updated_at"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # 3. Graph edges
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_edges",
        sa.Column("edge_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_node_id", sa.Uuid(), nullable=False),
        sa.Column("target_node_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column("relation_key", sa.String(length=64), nullable=False),
        sa.Column(
            "extraction_confidence",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "evidence_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "is_archived",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_node_id <> target_node_id", name="ck_knowledge_edges_no_self_loop"
        ),
        sa.CheckConstraint(
            "length(trim(relation_type)) > 0 AND length(relation_type) <= 64",
            name="ck_knowledge_edges_relation_type",
        ),
        sa.CheckConstraint(
            "length(trim(relation_key)) > 0", name="ck_knowledge_edges_relation_key"
        ),
        sa.CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="ck_knowledge_edges_confidence",
        ),
        sa.CheckConstraint(
            "evidence_count >= 0", name="ck_knowledge_edges_evidence_count"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_knowledge_edges_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_node_id"],
            ["knowledge_nodes.tenant_id", "knowledge_nodes.node_id"],
            name="fk_knowledge_edges_tenant_source",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "target_node_id"],
            ["knowledge_nodes.tenant_id", "knowledge_nodes.node_id"],
            name="fk_knowledge_edges_tenant_target",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("edge_id", name=op.f("pk_knowledge_edges")),
        sa.UniqueConstraint(
            "tenant_id", "edge_id", name="uq_knowledge_edges_tenant_edge"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_node_id",
            "target_node_id",
            "relation_key",
            name="uq_knowledge_edges_triple",
        ),
    )
    op.create_index(
        "ix_knowledge_edges_tenant_source",
        "knowledge_edges",
        ["tenant_id", "source_node_id", "is_archived"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_edges_tenant_target",
        "knowledge_edges",
        ["tenant_id", "target_node_id", "is_archived"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_edges_tenant_relation",
        "knowledge_edges",
        ["tenant_id", "relation_key"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # 4. Evidence (the authorization surface of the whole graph)
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_evidence",
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=True),
        sa.Column("edge_id", sa.Uuid(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column(
            "extraction_confidence",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("extraction_method", sa.String(length=32), nullable=False),
        sa.Column("extractor_version", sa.String(length=32), nullable=False),
        sa.Column(
            "is_valid", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(node_id IS NOT NULL AND edge_id IS NULL) "
            "OR (node_id IS NULL AND edge_id IS NOT NULL)",
            name="ck_knowledge_evidence_subject",
        ),
        sa.CheckConstraint(
            "length(trim(quote)) > 0 AND length(quote) <= 2000",
            name="ck_knowledge_evidence_quote",
        ),
        sa.CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="ck_knowledge_evidence_confidence",
        ),
        sa.CheckConstraint(
            "extraction_method IN ('RULES', 'RULES+SPACY', 'RULES+SPACY+LLM')",
            name="ck_knowledge_evidence_method",
        ),
        sa.CheckConstraint(
            "(is_valid AND invalidated_at IS NULL) "
            "OR (NOT is_valid AND invalidated_at IS NOT NULL)",
            name="ck_knowledge_evidence_validity",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_knowledge_evidence_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id", "chunk_id"],
            [
                "document_chunks.tenant_id",
                "document_chunks.document_id",
                "document_chunks.chunk_id",
            ],
            name="fk_knowledge_evidence_tenant_chunk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "node_id"],
            ["knowledge_nodes.tenant_id", "knowledge_nodes.node_id"],
            name="fk_knowledge_evidence_tenant_node",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "edge_id"],
            ["knowledge_edges.tenant_id", "knowledge_edges.edge_id"],
            name="fk_knowledge_evidence_tenant_edge",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("evidence_id", name=op.f("pk_knowledge_evidence")),
    )
    op.create_index(
        "ix_knowledge_evidence_tenant_node",
        "knowledge_evidence",
        ["tenant_id", "node_id", "is_valid"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_evidence_tenant_edge",
        "knowledge_evidence",
        ["tenant_id", "edge_id", "is_valid"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_evidence_tenant_document",
        "knowledge_evidence",
        ["tenant_id", "document_id", "is_valid"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_evidence_tenant_chunk",
        "knowledge_evidence",
        ["tenant_id", "chunk_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # 5. Aliases
    # ------------------------------------------------------------------
    op.create_table(
        "entity_aliases",
        sa.Column("alias_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("normalized_alias", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "extraction_confidence",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(alias)) > 0 AND length(alias) <= 255",
            name="ck_entity_aliases_alias",
        ),
        sa.CheckConstraint(
            "length(trim(normalized_alias)) > 0", name="ck_entity_aliases_normalized"
        ),
        sa.CheckConstraint(
            "source IN ('EXTRACTION', 'MANUAL', 'LLM')", name="ck_entity_aliases_source"
        ),
        sa.CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="ck_entity_aliases_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_entity_aliases_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "node_id"],
            ["knowledge_nodes.tenant_id", "knowledge_nodes.node_id"],
            name="fk_entity_aliases_tenant_node",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("alias_id", name=op.f("pk_entity_aliases")),
        sa.UniqueConstraint(
            "tenant_id",
            "node_id",
            "normalized_alias",
            name="uq_entity_aliases_node_alias",
        ),
    )
    op.create_index(
        "ix_entity_aliases_tenant_normalized",
        "entity_aliases",
        ["tenant_id", "normalized_alias"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # 6. Extraction jobs
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_extraction_jobs",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("extraction_method", sa.String(length=32), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False
        ),
        sa.Column(
            "chunks_processed", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "nodes_created", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "edges_created", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "evidence_created",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "evidence_invalidated",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'SKIPPED')",
            name="ck_kg_jobs_status",
        ),
        sa.CheckConstraint(
            "extraction_method IN ('RULES', 'RULES+SPACY', 'RULES+SPACY+LLM')",
            name="ck_kg_jobs_method",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts >= 1", name="ck_kg_jobs_attempts"
        ),
        sa.CheckConstraint(
            "chunks_processed >= 0 AND nodes_created >= 0 AND edges_created >= 0 "
            "AND evidence_created >= 0 AND evidence_invalidated >= 0",
            name="ck_kg_jobs_counters",
        ),
        sa.CheckConstraint(
            "error_message IS NULL OR length(error_message) <= 500",
            name="ck_kg_jobs_error_message",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_knowledge_extraction_jobs_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.document_id"],
            name="fk_kg_jobs_tenant_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_knowledge_extraction_jobs")),
    )
    op.create_index(
        "uq_kg_jobs_active_document",
        "knowledge_extraction_jobs",
        ["tenant_id", "document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'PROCESSING')"),
    )
    op.create_index(
        "ix_kg_jobs_tenant_status_created",
        "knowledge_extraction_jobs",
        ["tenant_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_kg_jobs_tenant_document",
        "knowledge_extraction_jobs",
        ["tenant_id", "document_id", "created_at"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # 7. Reviews, merges and conflicts
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_entity_reviews",
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("suggested_node_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_alias", sa.String(length=255), nullable=False),
        sa.Column(
            "similarity", sa.Float(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_kg_reviews_status",
        ),
        sa.CheckConstraint(
            "similarity >= 0 AND similarity <= 1", name="ck_kg_reviews_similarity"
        ),
        sa.CheckConstraint(
            "length(trim(candidate_alias)) > 0", name="ck_kg_reviews_candidate_alias"
        ),
        sa.CheckConstraint(
            "review_note IS NULL OR length(review_note) <= 500",
            name="ck_kg_reviews_note",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_knowledge_entity_reviews_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "node_id"],
            ["knowledge_nodes.tenant_id", "knowledge_nodes.node_id"],
            name="fk_kg_reviews_tenant_node",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("review_id", name=op.f("pk_knowledge_entity_reviews")),
    )
    op.create_index(
        "uq_kg_reviews_pending_candidate",
        "knowledge_entity_reviews",
        ["tenant_id", "node_id", "candidate_alias"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_kg_reviews_tenant_status",
        "knowledge_entity_reviews",
        ["tenant_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "knowledge_entity_merges",
        sa.Column("merge_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("source_node_id", sa.Uuid(), nullable=False),
        sa.Column("target_node_id", sa.Uuid(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "aliases_moved", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "edges_moved", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "evidence_moved", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("action IN ('MERGE', 'SPLIT')", name="ck_kg_merges_action"),
        sa.CheckConstraint(
            "action <> 'MERGE' OR target_node_id IS NOT NULL",
            name="ck_kg_merges_target_required",
        ),
        sa.CheckConstraint(
            "source_node_id <> target_node_id", name="ck_kg_merges_distinct_nodes"
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0 AND length(reason) <= 500",
            name="ck_kg_merges_reason",
        ),
        sa.CheckConstraint(
            "aliases_moved >= 0 AND edges_moved >= 0 AND evidence_moved >= 0",
            name="ck_kg_merges_counters",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_knowledge_entity_merges_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_node_id"],
            ["knowledge_nodes.tenant_id", "knowledge_nodes.node_id"],
            name="fk_kg_merges_tenant_source",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["users.tenant_id", "users.user_id"],
            name="fk_kg_merges_tenant_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("merge_id", name=op.f("pk_knowledge_entity_merges")),
    )
    op.create_index(
        "ix_kg_merges_tenant_created",
        "knowledge_entity_merges",
        ["tenant_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "knowledge_conflicts",
        sa.Column("conflict_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_node_id", sa.Uuid(), nullable=True),
        sa.Column("conflict_key", sa.String(length=200), nullable=False),
        sa.Column("relation_key", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column(
            "values",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "node_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "evidence_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'OPEN'"),
            nullable=False,
        ),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'RESOLVED', 'DISMISSED')",
            name="ck_kg_conflicts_status",
        ),
        sa.CheckConstraint(
            "length(trim(conflict_key)) > 0 AND length(conflict_key) <= 200",
            name="ck_kg_conflicts_key_length",
        ),
        sa.CheckConstraint(
            "length(description) <= 500", name="ck_kg_conflicts_description"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_knowledge_conflicts_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conflict_id", name=op.f("pk_knowledge_conflicts")),
        sa.UniqueConstraint(
            "tenant_id", "conflict_key", "relation_key", name="uq_kg_conflicts_key"
        ),
    )
    op.create_index(
        "ix_kg_conflicts_tenant_status",
        "knowledge_conflicts",
        ["tenant_id", "status", "detected_at"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # 8. Query analytics
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_query_analytics",
        sa.Column("query_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("search_mode", sa.String(length=20), nullable=False),
        sa.Column(
            "seed_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "node_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "edge_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "evidence_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "denied_documents",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "traversal_ms", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("total_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "retrieval_error",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "search_mode IN ('graph', 'graph_hybrid')",
            name="ck_kg_query_analytics_mode",
        ),
        sa.CheckConstraint(
            "seed_count >= 0 AND node_count >= 0 AND edge_count >= 0 "
            "AND evidence_count >= 0 AND denied_documents >= 0",
            name="ck_kg_query_analytics_counts",
        ),
        sa.CheckConstraint(
            "traversal_ms >= 0 AND total_ms >= 0",
            name="ck_kg_query_analytics_latency",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_knowledge_query_analytics_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("query_id", name=op.f("pk_knowledge_query_analytics")),
    )
    op.create_index(
        "ix_kg_query_analytics_tenant_created",
        "knowledge_query_analytics",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_kg_query_analytics_tenant_mode_created",
        "knowledge_query_analytics",
        ["tenant_id", "search_mode", "created_at"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # 9. Allow the two graph search modes in retrieval analytics
    # ------------------------------------------------------------------
    op.drop_constraint(
        "ck_retrieval_analytics_mode", "retrieval_analytics", type_="check"
    )
    op.create_check_constraint(
        "ck_retrieval_analytics_mode",
        "retrieval_analytics",
        "search_mode IN ('semantic', 'keyword', 'hybrid', 'graph', "
        "'graph_hybrid')",
    )

    # ------------------------------------------------------------------
    # 10. Row level security + least-privilege grants
    # ------------------------------------------------------------------
    for table in TENANT_TABLES:
        _enable_rls(table)

    for table in TENANT_TABLES:
        if table in APPEND_ONLY_TABLES:
            op.execute(sa.text(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}"))
        else:
            op.execute(
                sa.text(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {APP_ROLE}")
            )


def downgrade() -> None:
    op.drop_constraint(
        "ck_retrieval_analytics_mode", "retrieval_analytics", type_="check"
    )
    op.create_check_constraint(
        "ck_retrieval_analytics_mode",
        "retrieval_analytics",
        "search_mode IN ('semantic', 'keyword', 'hybrid')",
    )

    for table in TENANT_TABLES:
        op.execute(sa.text(f"REVOKE ALL ON {table} FROM {APP_ROLE}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    op.drop_index(
        "ix_kg_query_analytics_tenant_mode_created",
        table_name="knowledge_query_analytics",
    )
    op.drop_index(
        "ix_kg_query_analytics_tenant_created", table_name="knowledge_query_analytics"
    )
    op.drop_table("knowledge_query_analytics")

    op.drop_index("ix_kg_conflicts_tenant_status", table_name="knowledge_conflicts")
    op.drop_table("knowledge_conflicts")

    op.drop_index("ix_kg_merges_tenant_created", table_name="knowledge_entity_merges")
    op.drop_table("knowledge_entity_merges")

    op.drop_index(
        "ix_kg_reviews_tenant_status", table_name="knowledge_entity_reviews"
    )
    op.drop_index(
        "uq_kg_reviews_pending_candidate", table_name="knowledge_entity_reviews"
    )
    op.drop_table("knowledge_entity_reviews")

    op.drop_index(
        "ix_kg_jobs_tenant_document", table_name="knowledge_extraction_jobs"
    )
    op.drop_index(
        "ix_kg_jobs_tenant_status_created", table_name="knowledge_extraction_jobs"
    )
    op.drop_index(
        "uq_kg_jobs_active_document", table_name="knowledge_extraction_jobs"
    )
    op.drop_table("knowledge_extraction_jobs")

    op.drop_index(
        "ix_entity_aliases_tenant_normalized", table_name="entity_aliases"
    )
    op.drop_table("entity_aliases")

    op.drop_index(
        "ix_knowledge_evidence_tenant_chunk", table_name="knowledge_evidence"
    )
    op.drop_index(
        "ix_knowledge_evidence_tenant_document", table_name="knowledge_evidence"
    )
    op.drop_index(
        "ix_knowledge_evidence_tenant_edge", table_name="knowledge_evidence"
    )
    op.drop_index(
        "ix_knowledge_evidence_tenant_node", table_name="knowledge_evidence"
    )
    op.drop_table("knowledge_evidence")

    op.drop_index(
        "ix_knowledge_edges_tenant_relation", table_name="knowledge_edges"
    )
    op.drop_index(
        "ix_knowledge_edges_tenant_target", table_name="knowledge_edges"
    )
    op.drop_index(
        "ix_knowledge_edges_tenant_source", table_name="knowledge_edges"
    )
    op.drop_table("knowledge_edges")

    op.drop_index(
        "ix_knowledge_nodes_tenant_updated", table_name="knowledge_nodes"
    )
    op.drop_index(
        "ix_knowledge_nodes_tenant_normalized", table_name="knowledge_nodes"
    )
    op.drop_index("ix_knowledge_nodes_tenant_type", table_name="knowledge_nodes")
    op.drop_table("knowledge_nodes")

    op.drop_constraint(
        "uq_document_chunks_tenant_document_chunk",
        "document_chunks",
        type_="unique",
    )
