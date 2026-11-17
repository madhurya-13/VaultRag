"""Tenant- and user-owned RAG conversations.

A conversation is never shared implicitly. Ownership is enforced twice:

* ``UNIQUE (tenant_id, conversation_id, user_id)`` lets messages reference the
  full ownership triple, so a message cannot be attached to another user's
  conversation even by buggy application code;
* RLS restricts every row to the transaction-local tenant.

``deleted_at`` is a soft delete. The runtime role therefore keeps
``SELECT, INSERT, UPDATE`` and never needs ``DELETE`` on chat history.
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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    conversation_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(nullable=False)

    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Deterministic topic summary derived from user questions only. It must
    # never contain retrieved document text.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "conversation_id", name="uq_conversations_tenant_conversation"
        ),
        # Composite ownership key used by conversation_messages.
        UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "user_id",
            name="uq_conversations_tenant_conversation_user",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.user_id"],
            name="fk_conversations_tenant_user",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "title IS NULL OR (length(trim(title)) > 0 AND length(title) <= 120)",
            name="ck_conversations_title",
        ),
        CheckConstraint(
            "summary_message_count >= 0",
            name="ck_conversations_summary_count",
        ),
        Index("ix_conversations_tenant_user_updated", "tenant_id", "user_id", "updated_at"),
        Index("ix_conversations_tenant_deleted", "tenant_id", "deleted_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Conversation conversation_id={self.conversation_id} "
            f"tenant_id={self.tenant_id} user_id={self.user_id}>"
        )
