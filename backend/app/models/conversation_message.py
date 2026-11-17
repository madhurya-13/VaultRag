"""Messages belonging to a tenant- and user-owned conversation.

Only the user's question, the generated answer and citation *references* are
stored. Retrieval evidence (chunk text) is deliberately never persisted here:
conversation history must not become an authorization bypass or a second copy
of confidential document contents.

The composite foreign key includes ``user_id``, so a message cannot be
attached to another user's conversation at the database level.
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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

CONVERSATION_ROLES = ("user", "assistant", "system")


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    message_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)

    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Citation references only: document_id, chunk_id, page_start, page_end.
    citations: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    evidence_level: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id", "user_id"],
            [
                "conversations.tenant_id",
                "conversations.conversation_id",
                "conversations.user_id",
            ],
            name="fk_conversation_messages_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="ck_conversation_messages_role",
        ),
        CheckConstraint(
            "length(trim(content)) > 0",
            name="ck_conversation_messages_content_not_blank",
        ),
        CheckConstraint(
            "length(content) <= 20000",
            name="ck_conversation_messages_content_length",
        ),
        Index(
            "ix_conversation_messages_tenant_conversation_created",
            "tenant_id",
            "conversation_id",
            "created_at",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ConversationMessage message_id={self.message_id} "
            f"conversation_id={self.conversation_id} role={self.role!r}>"
        )
