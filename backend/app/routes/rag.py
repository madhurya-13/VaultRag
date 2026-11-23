"""Permission-aware advanced RAG API endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.request_context import RequestMeta, get_request_meta
from app.core.security_constants import CAP_VIEW_AUDIT_LOGS, POLICY_ALLOW
from app.dependencies.authentication import (
    AuthenticatedContext,
    get_current_auth,
    require_capability,
)
from app.schemas.rag import (
    RAGRetrievalExplanation,
    ConversationCitation,
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationMessageResponse,
    ConversationRenameRequest,
    ConversationResponse,
    RAGAskRequest,
    RAGAskResponse,
    RAGConflict,
    RAGEvidence,
    RAGHealthComponent,
    RAGHealthResponse,
    RAGHistoryItem,
    RAGHistoryResponse,
    RAGSource,
    RAGSourceGroup,
    RAGTimings,
    RetrievalAnalyticsResponse,
)
from app.services.context_builder import ContextSource
from app.services.embedding_service import get_embedding_service
from app.services.llm_service import get_llm_service
from app.services.rag.conversation_memory import (
    DEFAULT_TITLE,
    clear_conversation,
    count_messages,
    create_conversation,
    list_conversations,
    recent_messages,
    rename_conversation,
    require_conversation,
    soft_delete_conversation,
)
from app.services.rag.retrieval_analytics import (
    retrieval_analytics_summary,
)
from app.services.rag_service import answer_question
from app.services.security_audit import log_security_event
from app.services.tenant_settings import get_for_rag

router = APIRouter(prefix="/rag", tags=["Permission-Aware RAG"])

EXCERPT_CHARS = 400


def _source(
    source: ContextSource,
    versions: dict | None = None,
) -> RAGSource:
    excerpt = " ".join(source.text.split())[:EXCERPT_CHARS].strip()
    version = (versions or {}).get(source.document_id)
    return RAGSource(
        document_id=source.document_id,
        document_name=source.document_name,
        chunk_id=source.chunk_id,
        page_start=source.page_start,
        page_end=source.page_end,
        excerpt=excerpt or None,
        similarity=source.similarity,
        rerank_score=source.rerank_score,
        matched_by=list(source.matched_by),
        document_version_id=version[0] if version else None,
        version_number=version[1] if version else None,
        citation_verified=True,
    )


def _current_versions(db: Session, context: AuthenticatedContext, document_ids: set) -> dict:
    if not document_ids:
        return {}
    from app.integrations.models import DocumentVersion
    from sqlalchemy import select

    rows = db.execute(
        select(
            DocumentVersion.document_id,
            DocumentVersion.document_version_id,
            DocumentVersion.version_number,
        ).where(
            DocumentVersion.tenant_id == context.tenant_id,
            DocumentVersion.document_id.in_(document_ids),
            DocumentVersion.is_current.is_(True),
        )
    ).all()
    return {
        document_id: (version_id, number)
        for document_id, version_id, number in rows
    }


def _conversation_response(conversation, message_count: int) -> ConversationResponse:
    return ConversationResponse(
        conversation_id=conversation.conversation_id,
        title=conversation.title or DEFAULT_TITLE,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=message_count,
    )


def _citation_sources(raw: list, db: Session, context: AuthenticatedContext) -> list[RAGSource]:
    """Map stored citation references, dropping currently unauthorized ones.

    Conversation history is presentation state, not evidence. A citation whose
    document permission was revoked must not reappear in a later response as if
    it were still accessible.
    """
    from app.services.authorization import authorize_document

    sources: list[RAGSource] = []
    for item in raw or []:
        try:
            document_id = UUID(str(item["document_id"]))
            chunk_id = UUID(str(item["chunk_id"]))
            page_start = int(item["page_start"])
            page_end = int(item["page_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not authorize_document(db, context, document_id, "READ").allowed:
            continue
        sources.append(
            RAGSource(
                document_id=document_id,
                document_name=str(item.get("document_name") or "Document"),
                chunk_id=chunk_id,
                page_start=page_start,
                page_end=page_end,
            )
        )
    return sources


def _message_response(
    message, db: Session, context: AuthenticatedContext
) -> ConversationMessageResponse:
    from app.services.authorization import authorize_document

    citations: list[ConversationCitation] = []
    for item in message.citations or []:
        try:
            document_id = UUID(str(item["document_id"]))
            chunk_id = UUID(str(item["chunk_id"]))
            page_start = int(item["page_start"])
            page_end = int(item["page_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not authorize_document(db, context, document_id, "READ").allowed:
            continue
        version_id = item.get("document_version_id")
        version_number = item.get("version_number")
        citations.append(
            ConversationCitation(
                document_id=document_id,
                chunk_id=chunk_id,
                page_start=page_start,
                page_end=page_end,
                document_name=item.get("document_name"),
                document_version_id=UUID(str(version_id)) if version_id else None,
                version_number=int(version_number) if isinstance(version_number, int) else None,
                citation_verified=bool(item.get("citation_verified", False)),
            )
        )
    level = (
        message.evidence_level
        if message.evidence_level in {"strong", "moderate", "limited", "insufficient"}
        else None
    )
    return ConversationMessageResponse(
        message_id=message.message_id,
        role=message.role,
        content=message.content,
        citations=citations,
        evidence_level=level,
        created_at=message.created_at,
    )


@router.post(
    "/ask",
    response_model=RAGAskResponse,
    summary="Ask a question using only documents the caller may read",
)
def ask(
    payload: RAGAskRequest,
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> RAGAskResponse:
    # Authentication loaded the user, roles and tenant context. Commit this
    # read-only transaction before a potentially slow local LLM call; the RAG
    # service then uses its own short retrieval/audit transactions.
    db.commit()

    result = answer_question(
        context,
        payload.question,
        top_k=payload.top_k,
        search_mode=payload.search_mode,
        filters=payload.filters,
        conversation_id=payload.conversation_id,
        request_meta=request_meta,
        trace_limit=10,
    )

    from app.core.database import set_tenant_context

    set_tenant_context(db, context.tenant_id)
    versions = _current_versions(
        db, context, {source.document_id for source in result.sources}
    )
    try:
        feedback_enabled = bool(get_for_rag(db, context.tenant_id)["feedback_enabled"])
    except Exception:
        feedback_enabled = True

    conflict_sources = {
        conflict.topic: [_source(source, versions) for source in conflict.sources]
        for conflict in result.conflicts
    }
    trace = result.trace
    explanation = None
    if trace is not None:
        explanation = RAGRetrievalExplanation(
            mode=trace.search_mode,
            semantic_contribution=any(
                "semantic" in source.matched_by for source in result.sources
            ),
            keyword_contribution=any(
                "keyword" in source.matched_by for source in result.sources
            ),
            graph_contribution=trace.graph_used,
            reranked=trace.rerank_used,
            filters=dict(trace.filters),
            authorized_candidates=trace.authorized_count,
            denied_candidates=trace.denied_count,
            evidence_rationale=(
                f"Evidence level {result.evidence_level} from {result.retrieved_chunks} "
                f"authorized sources; {len(result.conflicts)} conflict(s) detected."
            ),
        )
    return RAGAskResponse(
        answer=result.answer,
        sources=[_source(source, versions) for source in result.sources],
        source_groups=[
            RAGSourceGroup(
                document_id=group.document_id,
                document_name=group.document_name,
                sources=[
                                        _source(
                            next(
                                source
                                for source in result.sources
                                if source.chunk_id == item.chunk_id
                            ),
                            versions,
                        )
                    for item in group.items
                    if any(
                        source.chunk_id == item.chunk_id
                        for source in result.sources
                    )
                ],
            )
            for group in result.source_groups
        ],
        retrieved_chunks=result.retrieved_chunks,
        model=result.model,
        search_mode=result.search_mode,
        evidence=RAGEvidence(
            level=result.evidence_level,
            score=max(0.0, min(1.0, result.evidence_score)),
            signals=result.evidence_signals,
        ),
        conflicts=[
            RAGConflict(
                topic=conflict.topic,
                description=conflict.description,
                values=list(conflict.values),
                sources=conflict_sources.get(conflict.topic, []),
            )
            for conflict in result.conflicts
        ],
        insufficient_evidence=result.insufficient_evidence,
        explanation=explanation,
        feedback_enabled=feedback_enabled,
        timings=RAGTimings(
            retrieval_ms=result.retrieval_ms,
            rerank_ms=result.rerank_ms,
            llm_ms=result.llm_ms,
            total_ms=result.total_ms,
        ),
        conversation_id=result.conversation_id,
        message_id=result.message_id,
    )


@router.get(
    "/health",
    response_model=RAGHealthResponse,
    summary="Operational status of the RAG dependencies",
)
def rag_health(
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> RAGHealthResponse:
    components: dict[str, RAGHealthComponent] = {}

    try:
        db.execute(text("SELECT 1"))
        components["postgresql"] = RAGHealthComponent(status="ok")
    except Exception:
        components["postgresql"] = RAGHealthComponent(
            status="unavailable", detail="Database is not reachable"
        )

    try:
        version = db.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one_or_none()
        if version:
            components["pgvector"] = RAGHealthComponent(
                status="ok", detail=f"pgvector {version}"
            )
        else:
            components["pgvector"] = RAGHealthComponent(
                status="unavailable", detail="pgvector extension is not installed"
            )
    except Exception:
        components["pgvector"] = RAGHealthComponent(
            status="unavailable", detail="pgvector status could not be checked"
        )

    try:
        fulltext = db.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'document_chunks' "
                "AND column_name = 'content_tsv'"
            )
        ).scalar_one_or_none()
        components["full_text_search"] = RAGHealthComponent(
            status="ready" if fulltext else "unavailable",
            detail=None if fulltext else "Milestone 7 migration is not applied",
        )
    except Exception:
        components["full_text_search"] = RAGHealthComponent(
            status="unavailable", detail="Full-text status could not be checked"
        )

    embedder = get_embedding_service()
    loaded = getattr(embedder, "_model", None) is not None
    components["embedding_model"] = RAGHealthComponent(
        status="ready" if loaded else "configured",
        detail=embedder.model_label,
    )

    try:
        ollama_status = get_llm_service().check_health()
        components["ollama"] = RAGHealthComponent(
            status=str(ollama_status.get("status", "unavailable")),
            detail=str(ollama_status.get("model", "")) or None,
        )
    except Exception:
        components["ollama"] = RAGHealthComponent(
            status="unavailable", detail="Ollama status could not be checked"
        )

    overall = (
        "ok"
        if all(
            component.status in {"ok", "ready", "configured"}
            for component in components.values()
        )
        else "degraded"
    )
    return RAGHealthResponse(status=overall, components=components)


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    summary="List the caller's conversations",
)
def get_conversations(
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> ConversationListResponse:
    pairs = list_conversations(db, context, limit=50)
    return ConversationListResponse(
        conversations=[
            _conversation_response(conversation, count)
            for conversation, count in pairs
        ]
    )


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a conversation owned by the caller",
)
def create_conversation_endpoint(
    payload: ConversationCreateRequest,
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> ConversationResponse:
    conversation = create_conversation(db, context, title=payload.title)
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="CONVERSATION",
        resource_id=conversation.conversation_id,
        action="CREATE",
        decision="ALLOW",
        reason="Conversation created",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )
    return _conversation_response(conversation, 0)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    summary="Read one caller-owned conversation and its messages",
)
def get_conversation_endpoint(
    conversation_id: UUID,
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> ConversationDetailResponse:
    conversation = require_conversation(db, context, conversation_id)
    messages = recent_messages(
        db, context, conversation_id, limit=50
    )
    return ConversationDetailResponse(
        conversation=_conversation_response(conversation, len(messages)),
        messages=[_message_response(message, db, context) for message in messages],
    )


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Rename a caller-owned conversation",
)
def rename_conversation_endpoint(
    conversation_id: UUID,
    payload: ConversationRenameRequest,
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> ConversationResponse:
    conversation = rename_conversation(db, context, conversation_id, payload.title)
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="CONVERSATION",
        resource_id=conversation.conversation_id,
        action="RENAME",
        decision="ALLOW",
        reason="Conversation renamed",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )
    return _conversation_response(
        conversation, count_messages(db, context, conversation_id)
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a caller-owned conversation",
)
def delete_conversation_endpoint(
    conversation_id: UUID,
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> Response:
    soft_delete_conversation(db, context, conversation_id)
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="CONVERSATION",
        resource_id=conversation_id,
        action="DELETE",
        decision="ALLOW",
        reason="Conversation soft-deleted",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{conversation_id}/clear",
    summary="Clear the messages of a caller-owned conversation",
)
def clear_conversation_endpoint(
    conversation_id: UUID,
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> dict[str, int]:
    cleared = clear_conversation(db, context, conversation_id)
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="CONVERSATION",
        resource_id=conversation_id,
        action="CLEAR",
        decision="ALLOW",
        reason=f"Cleared {cleared} messages",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )
    return {"cleared": cleared}


@router.get(
    "/history",
    response_model=RAGHistoryResponse,
    summary="Caller-scoped recent RAG history",
)
def history(
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> RAGHistoryResponse:
    pairs = list_conversations(db, context, limit=1)
    if not pairs:
        return RAGHistoryResponse(items=[], persistent=True)

    conversation, _ = pairs[0]
    messages = recent_messages(
        db, context, conversation.conversation_id, limit=20
    )

    items: list[RAGHistoryItem] = []
    pending_question: str | None = None
    for message in messages:
        if message.role == "user":
            pending_question = message.content
            continue
        if message.role != "assistant":
            continue
        level = (
            message.evidence_level
            if message.evidence_level
            in {"strong", "moderate", "limited", "insufficient"}
            else None
        )
        items.append(
            RAGHistoryItem(
                question=pending_question,
                answer=message.content,
                sources=_citation_sources(message.citations, db, context),
                conversation_id=conversation.conversation_id,
                evidence_level=level,
                created_at=message.created_at,
            )
        )
        pending_question = None

    return RAGHistoryResponse(items=items, persistent=True)


@router.get(
    "/analytics",
    response_model=RetrievalAnalyticsResponse,
    summary="Tenant-scoped retrieval analytics (ADMIN capability)",
)
def retrieval_analytics(
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    context: AuthenticatedContext = Depends(
        require_capability(CAP_VIEW_AUDIT_LOGS)
    ),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> RetrievalAnalyticsResponse:
    summary = retrieval_analytics_summary(
        db, tenant_id=context.tenant_id, days=days
    )
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="RETRIEVAL_ANALYTICS",
        resource_id=None,
        action="ANALYTICS_VIEW",
        decision="ALLOW",
        reason=f"Aggregated {summary['total_queries']} queries over {days} days",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )
    return RetrievalAnalyticsResponse(**summary)
