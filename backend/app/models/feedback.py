"""Answer feedback with an administrator review workflow.

Feedback is about an assistant message the caller owns. It never stores chunk
text, prompts, document excerpts or permission data. A retraction is a
timestamped soft delete; the runtime role has no DELETE grant.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

FEEDBACK_SENTIMENTS = ("POSITIVE", "NEGATIVE")
FEEDBACK_CATEGORIES = (
    "INCORRECT_ANSWER",
    "MISSING_CITATION",
    "WRONG_CITATION",
    "INCOMPLETE_ANSWER",
    "IRRELEVANT_SOURCES",
    "MISSING_DOCUMENT",
    "RETRIEVAL_PROBLEM",
    "PERMISSION_PROBLEM",
    "INGESTION_PROBLEM",
    "OTHER",
)
FEEDBACK_REVIEW_STATUSES = ("UNREVIEWED", "IN_REVIEW", "RESOLVED", "DISMISSED")

_CATEGORY_SQL = ", ".join(f"'{item}'" for item in FEEDBACK_CATEGORIES)
_SENTIMENT_SQL = ", ".join(f"'{item}'" for item in FEEDBACK_SENTIMENTS)
_REVIEW_SQL = ", ".join(f"'{item}'" for item in FEEDBACK_REVIEW_STATUSES)


class AnswerFeedback(Base):
    __tablename__ = "answer_feedback"

    feedback_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(nullable=False)
    message_id: Mapped[UUID] = mapped_column(nullable=False)

    category: Mapped[str] = mapped_column(String(40), nullable=False)
    sentiment: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="UNREVIEWED", server_default=text("'UNREVIEWED'")
    )
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_id: Mapped[UUID | None] = mapped_column(nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "feedback_id", name="uq_answer_feedback_tenant_feedback"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.user_id"],
            name="fk_answer_feedback_tenant_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "message_id"],
            ["conversation_messages.tenant_id", "conversation_messages.message_id"],
            name="fk_answer_feedback_tenant_message",
            ondelete="CASCADE",
        ),
        CheckConstraint(f"category IN ({_CATEGORY_SQL})", name="ck_answer_feedback_category"),
        CheckConstraint(f"sentiment IN ({_SENTIMENT_SQL})", name="ck_answer_feedback_sentiment"),
        CheckConstraint(f"review_status IN ({_REVIEW_SQL})", name="ck_answer_feedback_review"),
        CheckConstraint(
            "comment IS NULL OR (length(trim(comment)) > 0 AND length(comment) <= 2000)",
            name="ck_answer_feedback_comment",
        ),
        CheckConstraint(
            "review_notes IS NULL OR length(review_notes) <= 2000",
            name="ck_answer_feedback_review_notes",
        ),
        Index("ix_answer_feedback_tenant_created", "tenant_id", "created_at"),
        Index("ix_answer_feedback_tenant_review", "tenant_id", "review_status"),
        Index("ix_answer_feedback_tenant_message", "tenant_id", "message_id"),
        Index(
            "uq_answer_feedback_active_user_message",
            "tenant_id",
            "user_id",
            "message_id",
            unique=True,
            postgresql_where=text("retracted_at IS NULL"),
        ),
    )
