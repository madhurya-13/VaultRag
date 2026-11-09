"""Permission-aware RAG orchestration.

Sequence:
    validate caller -> query embedding -> short RLS retrieval transaction ->
    current authorization -> fetch authorized text -> rerank -> bounded context ->
    local LLM -> citation validation -> audit events -> response.

The LLM call happens outside the retrieval transaction. Unauthorized chunk text
is never selected, so it cannot reach context construction or the model.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal, set_tenant_context
from app.core.exceptions import BadRequestError, ForbiddenError, ServiceUnavailableError
from app.core.request_context import RequestMeta
from app.core.security_constants import POLICY_ALLOW
from app.dependencies.authentication import AuthenticatedContext
from app.services.citation_service import validate_citations
from app.services.context_builder import ContextSource, build_context
from app.services.embedding_service import EmbeddingService, get_embedding_service
from app.services.llm_service import OllamaLLMService, get_llm_service
from app.services.query_embedding import generate_query_embedding
from app.services.reranking import CrossEncoderReranker, rerank_authorized_chunks
from app.services.retrieval_authorization import (
    AuthorizedChunk,
    filter_authorized_chunks,
    load_retrieval_authorization_context,
)
from app.services.security_audit import log_security_event
from app.services.vector_retrieval import (
    fetch_chunk_texts,
    retrieve_candidate_chunks,
    validate_top_k,
)

logger = logging.getLogger(__name__)

NO_CONTEXT_ANSWER = (
    "I could not find sufficiently relevant information in the documents you "
    "are authorized to access."
)
UNVERIFIABLE_ANSWER = (
    "I could not produce a verifiable answer from the accessible documents."
)
SessionFactory = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class RAGResult:
    answer: str
    sources: tuple[ContextSource, ...]
    retrieved_chunks: int
    model: str | None


def _audit(
    context: AuthenticatedContext,
    request_meta: RequestMeta | None,
    *,
    resource_id: UUID,
    action: str,
    decision: str,
    reason: str,
    policy: str,
    session: Session | None = None,
) -> None:
    """Audit without ever storing question text or chunk text."""
    log_security_event(
        session,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="RAG_QUERY",
        resource_id=resource_id,
        action=action,
        decision=decision,
        reason=reason,
        policy=policy,
        request_meta=request_meta,
    )


def _authorized_chunks_with_text(
    db: Session,
    context: AuthenticatedContext,
    authorized_candidates,
) -> list[AuthorizedChunk]:
    chunk_ids = [candidate.chunk_id for candidate in authorized_candidates]
    texts = fetch_chunk_texts(db, context, chunk_ids)
    result: list[AuthorizedChunk] = []
    for candidate in authorized_candidates:
        text = texts.get(candidate.chunk_id)
        if text is None:
            continue
        result.append(
            AuthorizedChunk(
                chunk_id=candidate.chunk_id,
                tenant_id=candidate.tenant_id,
                document_id=candidate.document_id,
                chunk_index=candidate.chunk_index,
                page_start=candidate.page_start,
                page_end=candidate.page_end,
                distance=candidate.distance,
                document_name=candidate.document_name,
                text=text.text,
            )
        )
    return result


def _safe_no_context(
    context: AuthenticatedContext,
    request_meta: RequestMeta | None,
    *,
    query_id: UUID,
    reason: str,
) -> RAGResult:
    try:
        _audit(
            context,
            request_meta,
            resource_id=query_id,
            action="RAG_NO_CONTEXT",
            decision="ALLOW",
            reason=reason,
            policy=POLICY_ALLOW,
        )
    except Exception:
        logger.exception("Failed to audit no-context RAG response")
    return RAGResult(
        answer=NO_CONTEXT_ANSWER,
        sources=(),
        retrieved_chunks=0,
        model=None,
    )


def answer_question(
    context: AuthenticatedContext,
    question: str,
    *,
    top_k: int,
    request_meta: RequestMeta | None = None,
    session_factory: SessionFactory | None = None,
    embedding_service: EmbeddingService | None = None,
    llm_service: OllamaLLMService | None = None,
) -> RAGResult:
    settings = get_settings()
    factory = session_factory or SessionLocal
    query_id = uuid4()

    if not context.user.is_active:
        raise ForbiddenError("User is inactive")
    if not context.organization.is_active:
        raise ForbiddenError("Organization is inactive")

    limit = validate_top_k(top_k)
    embedder = embedding_service or get_embedding_service()

    # Validate the question before doing expensive work and before audit events.
    try:
        query = generate_query_embedding(
            question,
            embedding_service=embedder,
            max_chars=settings.rag_max_question_chars,
        )
    except BadRequestError:
        raise
    except ServiceUnavailableError:
        try:
            _audit(
                context,
                request_meta,
                resource_id=query_id,
                action="RAG_QUERY",
                decision="DENY",
                reason="Query embedding failed",
                policy="EMBEDDING_POLICY",
            )
        except Exception:
            logger.exception("Failed to audit embedding failure")
        raise

    try:
        _audit(
            context,
            request_meta,
            resource_id=query_id,
            action="RAG_QUERY_START",
            decision="ALLOW",
            reason="RAG query initiated",
            policy=POLICY_ALLOW,
        )
    except Exception:
        logger.exception("Failed to audit RAG query start")

    authorized: list[AuthorizedChunk] = []
    candidate_count = 0
    denied_count = 0

    try:
        with factory() as session:
            with session.begin():
                set_tenant_context(session, context.tenant_id)
                auth_context = load_retrieval_authorization_context(
                    session, context
                )
                candidates = retrieve_candidate_chunks(
                    session,
                    context,
                    query.vector,
                    top_k=limit,
                    model_label=query.model_label,
                    role_ids=auth_context.role_ids,
                )
                candidate_count = len(candidates)
                filter_result = filter_authorized_chunks(
                    session,
                    context,
                    candidates,
                    model_label=query.model_label,
                    authorization_context=auth_context,
                    request_meta=request_meta,
                )
                authorized = _authorized_chunks_with_text(
                    session, context, filter_result.authorized
                )
                denied_count = len(filter_result.denied)
                _audit(
                    context,
                    request_meta,
                    resource_id=query_id,
                    action="RAG_RETRIEVAL",
                    decision="ALLOW",
                    reason=(
                        f"candidates={candidate_count};"
                        f"authorized={len(filter_result.authorized)};"
                        f"denied={denied_count}"
                    ),
                    policy=POLICY_ALLOW,
                    session=session,
                )
    except Exception as exc:
        if isinstance(exc, (BadRequestError, ForbiddenError, ServiceUnavailableError)):
            raise
        logger.exception("Permission-aware retrieval failed")
        try:
            _audit(
                context,
                request_meta,
                resource_id=query_id,
                action="RAG_RETRIEVAL",
                decision="DENY",
                reason="Retrieval failed",
                policy="RETRIEVAL_FAILURE",
            )
        except Exception:
            logger.exception("Failed to audit retrieval failure")
        raise ServiceUnavailableError("Permission-aware retrieval failed") from exc

    if not authorized:
        return _safe_no_context(
            context,
            request_meta,
            query_id=query_id,
            reason=(
                f"candidates={candidate_count};authorized=0;denied={denied_count}"
            ),
        )

    reranker = None
    if settings.rag_rerank_enabled:
        reranker = CrossEncoderReranker(settings.rag_reranker_model)

    ranked = rerank_authorized_chunks(
        authorized,
        min_similarity=settings.rag_min_similarity,
        top_n=settings.rag_context_top_n,
        question=question,
        reranker=reranker,
    )
    if not ranked:
        return _safe_no_context(
            context,
            request_meta,
            query_id=query_id,
            reason="No authorized chunk met the relevance threshold",
        )

    counter = getattr(embedder, "count_tokens", None)
    built = build_context(
        ranked,
        max_tokens=settings.rag_max_context_tokens,
        token_counter=counter,
    )
    if not built.sources:
        return _safe_no_context(
            context,
            request_meta,
            query_id=query_id,
            reason="Authorized context did not fit the configured context budget",
        )

    try:
        _audit(
            context,
            request_meta,
            resource_id=query_id,
            action="RAG_LLM_START",
            decision="ALLOW",
            reason=f"context_sources={len(built.sources)}",
            policy=POLICY_ALLOW,
        )
    except Exception:
        logger.exception("Failed to audit LLM start")

    llm = llm_service or get_llm_service()
    started = time.monotonic()
    try:
        llm_response = llm.generate_answer(question, built.context)
    except ServiceUnavailableError:
        try:
            _audit(
                context,
                request_meta,
                resource_id=query_id,
                action="RAG_LLM",
                decision="DENY",
                reason="LLM generation failed",
                policy="LLM_OPERATIONAL_POLICY",
            )
        except Exception:
            logger.exception("Failed to audit LLM failure")
        raise
    except Exception as exc:
        logger.exception("Unexpected LLM failure")
        try:
            _audit(
                context,
                request_meta,
                resource_id=query_id,
                action="RAG_LLM",
                decision="DENY",
                reason="LLM generation failed",
                policy="LLM_OPERATIONAL_POLICY",
            )
        except Exception:
            logger.exception("Failed to audit LLM failure")
        raise ServiceUnavailableError("The local language model is unavailable") from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)
    citation = validate_citations(llm_response.answer, built.sources)
    try:
        _audit(
            context,
            request_meta,
            resource_id=query_id,
            action="RAG_CITATION_VALIDATION",
            decision="ALLOW" if citation.has_valid_citation else "DENY",
            reason=(
                f"valid_citations={len(citation.cited_source_ids)};"
                f"invalid_citations={len(citation.invalid_source_ids)};"
                f"latency_ms={elapsed_ms}"
            ),
            policy="CITATION_VALIDATION_POLICY",
        )
    except Exception:
        logger.exception("Failed to audit citation validation")

    if not citation.has_valid_citation:
        return RAGResult(
            answer=UNVERIFIABLE_ANSWER,
            sources=(),
            retrieved_chunks=0,
            model=llm_response.model,
        )

    try:
        _audit(
            context,
            request_meta,
            resource_id=query_id,
            action="RAG_LLM_COMPLETE",
            decision="ALLOW",
            reason=f"latency_ms={elapsed_ms};sources={len(citation.sources)}",
            policy=POLICY_ALLOW,
        )
    except Exception:
        logger.exception("Failed to audit LLM completion")

    return RAGResult(
        answer=citation.answer or UNVERIFIABLE_ANSWER,
        sources=citation.sources,
        retrieved_chunks=len(citation.sources),
        model=llm_response.model,
    )
