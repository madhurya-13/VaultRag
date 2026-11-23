"""Milestone 10: RAG evaluation datasets, runs and case results.

Revision ID: e1b2c3d4f506
Revises: d9a3f1b7c2e4
Create Date: 2026-11-23

Four tenant-scoped tables let an administrator import a labeled synthetic
dataset, execute it against the real permission-aware retrieval pipeline and
inspect per-case results. They never store chunk text, prompts or provider
tokens.

RLS is enabled and forced on every table, with the same fail-closed
``security.current_tenant_id()`` policy as the rest of the schema. The runtime
role receives SELECT/INSERT plus the minimum UPDATE required to advance a run
and refresh a dataset's query count; there is no DELETE grant, so an
administrator cannot erase evaluation history through the API role.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1b2c3d4f506"
down_revision: Union[str, None] = "d9a3f1b7c2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "vaultrag_app"

TENANT_TABLES = (
    "evaluation_datasets",
    "evaluation_cases",
    "evaluation_runs",
    "evaluation_results",
)

_CATEGORY_SQL = (
    "'single_document', 'multi_document', 'comparison', 'numeric', 'temporal', "
    "'conflicting', 'graph_relationship', 'insufficient_evidence', "
    "'unanswerable', 'restricted_document', 'metadata_filter', "
    "'document_version', 'permission_change', 'citation'"
)
_CONFIGURATION_SQL = (
    "'semantic', 'keyword', 'hybrid', 'hybrid_rerank', 'hybrid_graph', "
    "'hybrid_graph_rerank'"
)
_STATUS_SQL = "'PENDING', 'RUNNING', 'COMPLETED', 'FAILED'"


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
    op.create_table(
        "evaluation_datasets",
        sa.Column(
            "dataset_id", postgresql.UUID(as_uuid=True), primary_key=True
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "source", sa.String(20), nullable=False, server_default=sa.text("'SYNTHETIC'")
        ),
        sa.Column(
            "query_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "dataset_id", name="uq_evaluation_datasets_tenant_dataset"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "name",
            "version",
            name="uq_evaluation_datasets_tenant_name_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_evaluation_datasets_tenant_created_by",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version >= 1", name="ck_evaluation_datasets_version"),
        sa.CheckConstraint(
            "query_count >= 0", name="ck_evaluation_datasets_query_count"
        ),
        sa.CheckConstraint(
            "length(trim(name)) > 0 AND length(name) <= 200",
            name="ck_evaluation_datasets_name",
        ),
        sa.CheckConstraint(
            "source IN ('SYNTHETIC', 'IMPORTED')",
            name="ck_evaluation_datasets_source",
        ),
    )
    op.create_index(
        "ix_evaluation_datasets_tenant_created",
        "evaluation_datasets",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "evaluation_cases",
        sa.Column("case_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_key", sa.String(80), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_answer", sa.Text(), nullable=True),
        sa.Column(
            "expected_answerable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "allowed_document_names",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "gold_document_names",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "gold_chunks",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "expected_citation_document_names",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "restricted_document_names",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "filters",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "dataset_id", "case_key", name="uq_evaluation_cases_key"
        ),
        sa.UniqueConstraint(
            "tenant_id", "case_id", name="uq_evaluation_cases_tenant_case"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["evaluation_datasets.tenant_id", "evaluation_datasets.dataset_id"],
            name="fk_evaluation_cases_dataset",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"category IN ({_CATEGORY_SQL})", name="ck_evaluation_cases_category"
        ),
        sa.CheckConstraint(
            "length(trim(case_key)) > 0 AND length(case_key) <= 80",
            name="ck_evaluation_cases_key",
        ),
        sa.CheckConstraint(
            "length(trim(question)) > 0 AND length(question) <= 4000",
            name="ck_evaluation_cases_question",
        ),
    )
    op.create_index(
        "ix_evaluation_cases_tenant_dataset",
        "evaluation_cases",
        ["tenant_id", "dataset_id"],
    )
    op.create_index(
        "ix_evaluation_cases_tenant_category",
        "evaluation_cases",
        ["tenant_id", "category"],
    )

    op.create_table(
        "evaluation_runs",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("configuration", sa.String(40), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column(
            "answer_mode",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'retrieval'"),
        ),
        sa.Column(
            "judge_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("viewer_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column(
            "case_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "completed_cases", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "failed_cases", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "authorization_violations",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "metrics",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "run_id", name="uq_evaluation_runs_tenant_run"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["evaluation_datasets.tenant_id", "evaluation_datasets.dataset_id"],
            name="fk_evaluation_runs_dataset",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_evaluation_runs_created_by",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"configuration IN ({_CONFIGURATION_SQL})",
            name="ck_evaluation_runs_configuration",
        ),
        sa.CheckConstraint(f"status IN ({_STATUS_SQL})", name="ck_evaluation_runs_status"),
        sa.CheckConstraint(
            "answer_mode IN ('retrieval', 'generated')",
            name="ck_evaluation_runs_answer_mode",
        ),
        sa.CheckConstraint("top_k BETWEEN 1 AND 50", name="ck_evaluation_runs_top_k"),
        sa.CheckConstraint(
            "case_count >= 0 AND completed_cases >= 0 AND failed_cases >= 0 "
            "AND authorization_violations >= 0",
            name="ck_evaluation_runs_counts",
        ),
    )
    op.create_index(
        "ix_evaluation_runs_tenant_created",
        "evaluation_runs",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_evaluation_runs_tenant_dataset_created",
        "evaluation_runs",
        ["tenant_id", "dataset_id", "created_at"],
    )

    op.create_table(
        "evaluation_results",
        sa.Column("result_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "retrieved_chunk_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "retrieved_document_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column(
            "citations",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "metrics",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "passed", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column(
            "latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "run_id", "case_id", name="uq_evaluation_results_case"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["evaluation_runs.tenant_id", "evaluation_runs.run_id"],
            name="fk_evaluation_results_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["evaluation_cases.tenant_id", "evaluation_cases.case_id"],
            name="fk_evaluation_results_case_ref",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("latency_ms >= 0", name="ck_evaluation_results_latency"),
    )
    op.create_index(
        "ix_evaluation_results_tenant_run",
        "evaluation_results",
        ["tenant_id", "run_id"],
    )

    for table in TENANT_TABLES:
        _enable_rls(table)

    # Datasets may gain cases after import; runs advance state; results are
    # insert-only. No DELETE: evaluation history is not erasable through the
    # runtime role.
    op.execute(
        sa.text(
            f"GRANT SELECT, INSERT, UPDATE ON evaluation_datasets TO {APP_ROLE}"
        )
    )
    op.execute(
        sa.text(f"GRANT SELECT, INSERT ON evaluation_cases TO {APP_ROLE}")
    )
    op.execute(
        sa.text(f"GRANT SELECT, INSERT, UPDATE ON evaluation_runs TO {APP_ROLE}")
    )
    op.execute(
        sa.text(f"GRANT SELECT, INSERT ON evaluation_results TO {APP_ROLE}")
    )


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.execute(sa.text(f"REVOKE ALL ON {table} FROM {APP_ROLE}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    op.drop_index("ix_evaluation_results_tenant_run", table_name="evaluation_results")
    op.drop_table("evaluation_results")
    op.drop_index(
        "ix_evaluation_runs_tenant_dataset_created", table_name="evaluation_runs"
    )
    op.drop_index("ix_evaluation_runs_tenant_created", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
    op.drop_index("ix_evaluation_cases_tenant_category", table_name="evaluation_cases")
    op.drop_index("ix_evaluation_cases_tenant_dataset", table_name="evaluation_cases")
    op.drop_table("evaluation_cases")
    op.drop_index(
        "ix_evaluation_datasets_tenant_created", table_name="evaluation_datasets"
    )
    op.drop_table("evaluation_datasets")
