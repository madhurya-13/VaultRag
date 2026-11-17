"""Controlled conversational memory.

Security model
--------------
* Every conversation belongs to exactly one authenticated user and tenant. A
  missing or foreign conversation resolves to "not found" rather than leaking
  existence.
* Only the user question, generated answer and citation *references* are
  stored. Retrieved chunk text is never persisted.
* History is used to resolve follow-up topics (for example "how many days can I
  take?"). It is **not** evidence: every turn performs fresh retrieval and fresh
  authorization. An answer whose document permission was revoked cannot be
  replayed from history.
* Summaries are deterministic and derived from user questions only, so they
  cannot contain confidential document text.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.dependencies.authentication import AuthenticatedContext
from app.models import Conversation, ConversationMessage
from app.services.rag.query_understanding import analyze_query

DEFAULT_TITLE = "New conversation"
MAX_TITLE_CHARS = 120
MAX_CONTENT_CHARS = 20000
MAX_SUMMARY_CHARS = 1000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_title(question: str, *, max_chars: int = MAX_TITLE_CHARS) -> str:
    compact = " ".join((question or "").split())
    title = compact[:max_chars].strip()
    return title or DEFAULT_TITLE


def normalize_title(title: str | None) -> str | None:
    if title is None:
        return None
    normalized = " ".join(title.split())
    if not normalized:
        raise BadRequestError("Conversation title cannot be blank")
    if len(normalized) > MAX_TITLE_CHARS:
        raise BadRequestError(
            f"Conversation title must be at most {MAX_TITLE_CHARS} characters"
        )
    return normalized


def create_conversation(
    db: Session,
    context: AuthenticatedContext,
    *,
    title: str | None = None,
) -> Conversation:
    conversation = Conversation(
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        title=normalize_title(title),
    )
    db.add(conversation)
    db.flush()
    db.refresh(conversation)
    return conversation


def get_conversation(
    db: Session,
    context: AuthenticatedContext,
    conversation_id: UUID,
) -> Conversation | None:
    return db.scalar(
        select(Conversation).where(
            Conversation.tenant_id == context.tenant_id,
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == context.user.user_id,
            Conversation.deleted_at.is_(None),
        )
    )


def require_conversation(
    db: Session,
    context: AuthenticatedContext,
    conversation_id: UUID,
) -> Conversation:
    conversation = get_conversation(db, context, conversation_id)
    if conversation is None:
        # Same response for "does not exist" and "belongs to someone else".
        raise NotFoundError("Conversation not found")
    return conversation


def list_conversations(
    db: Session,
    context: AuthenticatedContext,
    *,
    limit: int = 50,
) -> list[tuple[Conversation, int]]:
    bounded = max(1, min(limit, 100))
    counts = dict(
        db.execute(
            select(
                ConversationMessage.conversation_id,
                func.count(),
            )
            .where(
                ConversationMessage.tenant_id == context.tenant_id,
                ConversationMessage.user_id == context.user.user_id,
                ConversationMessage.deleted_at.is_(None),
            )
            .group_by(ConversationMessage.conversation_id)
        ).all()
    )
    conversations = list(
        db.scalars(
            select(Conversation)
            .where(
                Conversation.tenant_id == context.tenant_id,
                Conversation.user_id == context.user.user_id,
                Conversation.deleted_at.is_(None),
            )
            .order_by(
                Conversation.updated_at.desc(),
                Conversation.conversation_id,
            )
            .limit(bounded)
        ).all()
    )
    return [
        (conversation, int(counts.get(conversation.conversation_id, 0)))
        for conversation in conversations
    ]


def count_messages(
    db: Session,
    context: AuthenticatedContext,
    conversation_id: UUID,
) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(ConversationMessage)
            .where(
                ConversationMessage.tenant_id == context.tenant_id,
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.user_id == context.user.user_id,
                ConversationMessage.deleted_at.is_(None),
            )
        )
        or 0
    )


def rename_conversation(
    db: Session,
    context: AuthenticatedContext,
    conversation_id: UUID,
    title: str,
) -> Conversation:
    conversation = require_conversation(db, context, conversation_id)
    conversation.title = normalize_title(title)
    conversation.updated_at = _now()
    db.flush()
    return conversation


def soft_delete_conversation(
    db: Session,
    context: AuthenticatedContext,
    conversation_id: UUID,
) -> None:
    conversation = require_conversation(db, context, conversation_id)
    conversation.deleted_at = _now()
    conversation.is_archived = True
    conversation.updated_at = _now()
    db.flush()


def clear_conversation(
    db: Session,
    context: AuthenticatedContext,
    conversation_id: UUID,
) -> int:
    """Soft-delete every message in a conversation owned by the caller."""
    conversation = require_conversation(db, context, conversation_id)
    result = db.execute(
        update(ConversationMessage)
        .where(
            ConversationMessage.tenant_id == context.tenant_id,
            ConversationMessage.conversation_id == conversation.conversation_id,
            ConversationMessage.user_id == context.user.user_id,
            ConversationMessage.deleted_at.is_(None),
        )
        .values(deleted_at=_now())
        .execution_options(synchronize_session=False)
    )
    conversation.updated_at = _now()
    conversation.summary = None
    conversation.summary_message_count = 0
    db.flush()
    return int(result.rowcount or 0)


def add_message(
    db: Session,
    conversation: Conversation,
    *,
    role: str,
    content: str,
    citations: list[dict] | None = None,
    evidence_level: str | None = None,
) -> ConversationMessage:
    normalized_role = role.strip().lower()
    if normalized_role not in {"user", "assistant", "system"}:
        raise ValueError("Unsupported conversation message role")
    text = (content or "").strip()
    if not text:
        raise ValueError("Conversation message content cannot be blank")
    if len(text) > MAX_CONTENT_CHARS:
        text = text[:MAX_CONTENT_CHARS]

    message = ConversationMessage(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.conversation_id,
        user_id=conversation.user_id,
        role=normalized_role,
        content=text,
        citations=list(citations or []),
        evidence_level=evidence_level,
    )
    db.add(message)
    conversation.updated_at = _now()
    db.flush()
    db.refresh(message)
    return message


def recent_messages(
    db: Session,
    context: AuthenticatedContext,
    conversation_id: UUID,
    *,
    limit: int,
) -> list[ConversationMessage]:
    if limit <= 0:
        return []
    rows = list(
        db.scalars(
            select(ConversationMessage)
            .where(
                ConversationMessage.tenant_id == context.tenant_id,
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.user_id == context.user.user_id,
                ConversationMessage.deleted_at.is_(None),
            )
            .order_by(
                ConversationMessage.created_at.desc(),
                ConversationMessage.message_id.desc(),
            )
            .limit(min(limit, 50))
        ).all()
    )
    return list(reversed(rows))


def conversation_topic(
    messages: list[ConversationMessage],
    summary: str | None = None,
    *,
    limit: int = 8,
) -> str | None:
    """Derive a compact topic string from user questions only."""
    parts: list[str] = []
    if summary:
        parts.append(summary)

    for message in messages:
        if message.role != "user":
            continue
        analysis = analyze_query(message.content)
        parts.extend(analysis.keywords[:4])

    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        for token in part.replace("Topics:", " ").replace(",", " ").split():
            cleaned = token.strip(". ").lower()
            if len(cleaned) < 2 or cleaned in seen:
                continue
            seen.add(cleaned)
            unique.append(cleaned)
        if len(unique) >= limit:
            break
    return " ".join(unique[:limit]) or None


def summarize_conversation(
    db: Session,
    conversation: Conversation,
    messages: list[ConversationMessage],
    *,
    summary_turns: int,
) -> bool:
    """Create a deterministic, question-only summary for a long session."""
    user_messages = [message for message in messages if message.role == "user"]
    if len(user_messages) < summary_turns:
        return False

    topics: list[str] = []
    for message in user_messages[-summary_turns:]:
        analysis = analyze_query(message.content)
        for keyword in analysis.keywords[:3]:
            if keyword.lower() not in {item.lower() for item in topics}:
                topics.append(keyword)

    latest = " ".join(user_messages[-1].content.split())[:300]
    summary = (
        f"Topics: {', '.join(topics[:10])}. Latest question: {latest}"
    )[:MAX_SUMMARY_CHARS]
    conversation.summary = summary
    conversation.summary_message_count = len(messages)
    conversation.updated_at = _now()
    db.flush()
    return True
