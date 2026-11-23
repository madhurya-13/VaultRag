"""Tenant-scoped RAG evaluation datasets, runs and case results.

Evaluation is a first-class product workflow in Milestone 10. It deliberately
stores **references**, not content copies:

* a case stores the question, an optional expected answer, category labels and
  the names/indices of the documents that should support the answer;
* a result stores retrieved IDs, the generated answer, citation references and
  computed metrics.

Chunk text is never copied into evaluation tables. Document names are stored as
labels so a dataset remains readable after a document is renamed; the runner
resolves them against the tenant's current documents and re-authorizes every
retrieval through the normal pipeline.

RLS is enabled and forced by the migration; every query is also filtered by
``tenant_id`` in the service layer.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

EVALUATION_STATUS_PENDING = "PENDING"
EVALUATION_STATUS_RUNNING = "RUNNING"
EVALUATION_STATUS_COMPLETED = "COMPLETED"
EVALUATION_STATUS_FAILED = "FAILED"

EVALUATION_CATEGORIES = (
    "single_document",
    "multi_document",
    "comparison",
    "numeric",
    "temporal",
    "conflicting",
    "graph_relationship",
    "insufficient_evidence",
    "unanswerable",
    "restricted_document",
    "metadata_filter",
    "document_version",
    "permission_change",
    "citation",
)

_CATEGORY_SQL = ", ".join(f"'{category}'" for category in EVALUATION_CATEGORIES)
_STATUS_SQL = "'PENDING', 'RUNNING', 'COMPLETED', 'FAILED'"
_ANSWER_MODE_SQL = "'retrieval', 'generated'"


class EvaluationDataset(Base):
    """A versioned, tenant-owned evaluation dataset."""

    __tablename__ = "evaluation_datasets"

    dataset_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="SYNTHETIC", server_default=text("'SYNTHETIC'")
    )
    query_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "dataset_id", name="uq_evaluation_datasets_tenant_dataset"
        ),
        UniqueConstraint(
            "tenant_id",
            "name",
            "version",
            name="uq_evaluation_datasets_tenant_name_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_evaluation_datasets_tenant_created_by",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_evaluation_datasets_version"),
        CheckConstraint("query_count >= 0", name="ck_evaluation_datasets_query_count"),
        CheckConstraint(
            "length(trim(name)) > 0 AND length(name) <= 200",
            name="ck_evaluation_datasets_name",
        ),
        CheckConstraint(
            "source IN ('SYNTHETIC', 'IMPORTED')",
            name="ck_evaluation_datasets_source",
        ),
        Index("ix_evaluation_datasets_tenant_created", "tenant_id", "created_at"),
    )


class EvaluationCase(Base):
    """One labeled question inside a dataset.

    ``gold_chunks`` is a list of ``{"document_name": ..., "chunk_index": ...}``
    objects. Resolving them at run time keeps the dataset portable across a
    re-ingested corpus while still allowing chunk-level metrics.
    """

    __tablename__ = "evaluation_cases"

    case_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(nullable=False)
    case_key: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_answerable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    allowed_document_names: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    gold_document_names: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    gold_chunks: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    expected_citation_document_names: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    restricted_document_names: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    filters: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "dataset_id", "case_key", name="uq_evaluation_cases_key"
        ),
        UniqueConstraint(
            "tenant_id", "case_id", name="uq_evaluation_cases_tenant_case"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["evaluation_datasets.tenant_id", "evaluation_datasets.dataset_id"],
            name="fk_evaluation_cases_dataset",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"category IN ({_CATEGORY_SQL})", name="ck_evaluation_cases_category"
        ),
        CheckConstraint(
            "length(trim(case_key)) > 0 AND length(case_key) <= 80",
            name="ck_evaluation_cases_key",
        ),
        CheckConstraint(
            "length(trim(question)) > 0 AND length(question) <= 4000",
            name="ck_evaluation_cases_question",
        ),
        Index("ix_evaluation_cases_tenant_dataset", "tenant_id", "dataset_id"),
        Index("ix_evaluation_cases_tenant_category", "tenant_id", "category"),
    )


class EvaluationRun(Base):
    """One execution of a dataset under one named configuration."""

    __tablename__ = "evaluation_runs"

    run_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(nullable=False)
    configuration: Mapped[str] = mapped_column(String(40), nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    answer_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="retrieval", server_default=text("'retrieval'")
    )
    judge_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    viewer_user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=EVALUATION_STATUS_PENDING,
        server_default=text("'PENDING'"),
    )
    case_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    completed_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    failed_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    authorization_violations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    metrics: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "run_id", name="uq_evaluation_runs_tenant_run"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["evaluation_datasets.tenant_id", "evaluation_datasets.dataset_id"],
            name="fk_evaluation_runs_dataset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_evaluation_runs_created_by",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "configuration IN ('semantic', 'keyword', 'hybrid', 'hybrid_rerank', "
            "'hybrid_graph', 'hybrid_graph_rerank')",
            name="ck_evaluation_runs_configuration",
        ),
        CheckConstraint(
            f"status IN ({_STATUS_SQL})", name="ck_evaluation_runs_status"
        ),
        CheckConstraint(
            f"answer_mode IN ({_ANSWER_MODE_SQL})",
            name="ck_evaluation_runs_answer_mode",
        ),
        CheckConstraint("top_k BETWEEN 1 AND 50", name="ck_evaluation_runs_top_k"),
        CheckConstraint(
            "case_count >= 0 AND completed_cases >= 0 AND failed_cases >= 0 "
            "AND authorization_violations >= 0",
            name="ck_evaluation_runs_counts",
        ),
        Index("ix_evaluation_runs_tenant_created", "tenant_id", "created_at"),
        Index(
            "ix_evaluation_runs_tenant_dataset_created",
            "tenant_id",
            "dataset_id",
            "created_at",
        ),
    )


class EvaluationResult(Base):
    """Per-case metrics and references for one run.

    The answer is stored because it is the artifact under evaluation. Chunk
    text, prompts and provider responses are not stored.
    """

    __tablename__ = "evaluation_results"

    result_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    case_id: Mapped[UUID] = mapped_column(nullable=False)
    retrieved_chunk_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    retrieved_document_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    metrics: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    passed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "run_id", "case_id", name="uq_evaluation_results_case"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["evaluation_runs.tenant_id", "evaluation_runs.run_id"],
            name="fk_evaluation_results_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["evaluation_cases.tenant_id", "evaluation_cases.case_id"],
            name="fk_evaluation_results_case_ref",
            ondelete="CASCADE",
        ),
        CheckConstraint("latency_ms >= 0", name="ck_evaluation_results_latency"),
        Index("ix_evaluation_results_tenant_run", "tenant_id", "run_id"),
    )
