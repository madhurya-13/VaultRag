"""Administrator review, merge/split audit and conflict records.

Three concerns share this module because they are one workflow: a candidate that
extraction could not resolve safely becomes a review row, an administrator's
decision is recorded as an immutable merge/split audit row, and contradictions
between documents are surfaced as conflicts.

Nothing here grants access to document text. A conflict or review stores node
identifiers, relation labels and short value strings; the API re-checks evidence
visibility before returning any of it.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
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


class KnowledgeEntityReview(Base):
    """A possible duplicate that needs a human decision.

    Fuzzy matches (including same-name PERSON mentions without a shared anchor)
    stop here instead of merging automatically.
    """

    __tablename__ = "knowledge_entity_reviews"

    review_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[UUID] = mapped_column(nullable=False)
    # Advisory pointer only: a review must survive the deletion of the node it
    # suggests, and a composite NOT NULL tenant FK cannot be SET NULL. The
    # service layer validates that a suggestion exists in the same tenant.
    suggested_node_id: Mapped[UUID | None] = mapped_column(nullable=True)

    candidate_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    similarity: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDING'")
    )

    reviewed_by: Mapped[UUID | None] = mapped_column(nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "node_id"],
            ["knowledge_nodes.tenant_id", "knowledge_nodes.node_id"],
            name="fk_kg_reviews_tenant_node",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_kg_reviews_status",
        ),
        CheckConstraint(
            "similarity >= 0 AND similarity <= 1", name="ck_kg_reviews_similarity"
        ),
        CheckConstraint(
            "length(trim(candidate_alias)) > 0",
            name="ck_kg_reviews_candidate_alias",
        ),
        CheckConstraint(
            "review_note IS NULL OR length(review_note) <= 500",
            name="ck_kg_reviews_note",
        ),
        Index(
            "uq_kg_reviews_pending_candidate",
            "tenant_id",
            "node_id",
            "candidate_alias",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index("ix_kg_reviews_tenant_status", "tenant_id", "status", "created_at"),
    )


class KnowledgeEntityMerge(Base):
    """Immutable audit row for an administrator merge or alias split."""

    __tablename__ = "knowledge_entity_merges"

    merge_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    source_node_id: Mapped[UUID] = mapped_column(nullable=False)
    target_node_id: Mapped[UUID | None] = mapped_column(nullable=True)
    actor_user_id: Mapped[UUID] = mapped_column(nullable=False)

    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    aliases_moved: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    edges_moved: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    evidence_moved: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "source_node_id"],
            ["knowledge_nodes.tenant_id", "knowledge_nodes.node_id"],
            name="fk_kg_merges_tenant_source",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["users.tenant_id", "users.user_id"],
            name="fk_kg_merges_tenant_actor",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "action IN ('MERGE', 'SPLIT')", name="ck_kg_merges_action"
        ),
        CheckConstraint(
            "action <> 'MERGE' OR target_node_id IS NOT NULL",
            name="ck_kg_merges_target_required",
        ),
        CheckConstraint(
            "source_node_id <> target_node_id",
            name="ck_kg_merges_distinct_nodes",
        ),
        CheckConstraint(
            "length(trim(reason)) > 0 AND length(reason) <= 500",
            name="ck_kg_merges_reason",
        ),
        CheckConstraint(
            "aliases_moved >= 0 AND edges_moved >= 0 AND evidence_moved >= 0",
            name="ck_kg_merges_counters",
        ),
        Index("ix_kg_merges_tenant_created", "tenant_id", "created_at"),
    )


class KnowledgeConflict(Base):
    """A contradiction between two sets of authorized evidence.

    ``values`` holds short, already-authorized value strings. The API never
    returns a conflict whose evidence is not visible to the caller.
    """

    __tablename__ = "knowledge_conflicts"

    conflict_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    # Advisory pointer only, for the same reason as suggested_node_id above.
    subject_node_id: Mapped[UUID | None] = mapped_column(nullable=True)
    conflict_key: Mapped[str] = mapped_column(String(200), nullable=False)
    relation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)

    values: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    node_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    evidence_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'OPEN'")
    )
    resolved_by: Mapped[UUID | None] = mapped_column(nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "conflict_key",
            "relation_key",
            name="uq_kg_conflicts_key",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'RESOLVED', 'DISMISSED')",
            name="ck_kg_conflicts_status",
        ),
        CheckConstraint(
            "length(trim(conflict_key)) > 0 AND length(conflict_key) <= 200",
            name="ck_kg_conflicts_key_length",
        ),
        CheckConstraint(
            "length(description) <= 500", name="ck_kg_conflicts_description"
        ),
        Index("ix_kg_conflicts_tenant_status", "tenant_id", "status", "detected_at"),
    )
