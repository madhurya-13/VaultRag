"""Permission-aware advanced RAG orchestration.

Sequence
--------
validate caller -> resolve caller-owned conversation (optional) -> query
understanding/rewrite/expansion/decomposition -> query embeddings -> short RLS
retrieval transaction (semantic + keyword + RRF) -> current authorization ->
authorized text fetch -> rerank/merge/diversify -> evidence grouping ->
multi-document reasoning -> local LLM -> citation + claim validation ->
post-generation re-authorization -> evidence assessment -> conversation
persistence -> analytics -> audit -> response.

Invariants
----------
* Candidate SQL never selects chunk text.
* Text is fetched only for chunk IDs that passed current authorization.
* The reranker, context builder and LLM only ever see authorized text.
* Conversation history is never used as evidence; every turn re-retrieves and
  re-authorizes.
* The LLM call happens outside the retrieval transaction.
* Citations are validated against the backend's authorized source list, and the
  cited documents are re-authorized before the response is returned.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field, replace
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal, set_tenant_context
from app.core.exceptions import (
    BadRequestError,
    ForbiddenError,
    ServiceUnavailableError,
)
from app.core.request_context import RequestMeta
from app.core.security_constants import POLICY_ALLOW
from app.dependencies.authentication import AuthenticatedContext
from app.schemas.rag import RAGFilters
from app.services.context_builder import ContextSource, build_context
from app.services.embedding_service import EmbeddingService, get_embedding_service
from app.services.llm_service import OllamaLLMService, get_llm_service
from app.services.prompt_builder import build_prompt
from app.services.query_embedding import generate_query_embedding, validate_question
from app.services.reranking import CrossEncoderReranker, SupportsRerank
from app.services.retrieval_authorization import (
    AuthorizedChunk,
    filter_authorized_chunks,
    load_retrieval_authorization_context,
)
from app.services.security_audit import log_security_event
from app.services.rag.citation_validator import audit_answer, reauthorize_sources
from app.services.rag.conversation_memory import (
    add_message,
    build_title,
    conversation_topic,
    recent_messages,
    require_conversation,
    summarize_conversation,
)
from app.services.rag.evidence_builder import (
    EvidenceGroup,
    build_evidence_items,
    citations_payload,
    group_evidence,
)
from app.services.rag.evidence_evaluator import LEVEL_INSUFFICIENT, assess_evidence
from app.services.rag.hybrid_retriever import fuse_candidate_lists
from app.services.rag.keyword_retriever import retrieve_keyword_candidates
from app.services.rag.multi_document_reasoner import (
    Conflict,
    detect_conflicts,
    reasoning_instructions,
)
from app.services.rag.query_decomposer import build_query_plan
from app.services.rag.reranker import rerank_evidence
from app.services.rag.retrieval_analytics import record_retrieval_metrics
from app.services.rag.semantic_retriever import retrieve_semantic_candidates
from app.services.vector_retrieval import fetch_chunk_texts, validate_top_k

logger = logging.getLogger(__name__)

NO_CONTEXT_ANSWER = (
    "I could not find sufficiently relevant information in the documents you "
    "are authorized to access."
)
UNVERIFIABLE_ANSWER = (
    "I could not produce a verifiable answer from the accessible documents."
)
INSUFFICIENT_EVIDENCE_ANSWER = (
    "I could not find sufficient information in the documents you are "
    "authorized to access."
)
SessionFactory = Callable[[], Session]

VALID_SEARCH_MODES = frozenset({"semantic", "keyword", "hybrid"})
MAX_QUERY_VARIANTS = 5
MAX_KEYWORD_VARIANTS = 4


@dataclass(frozen=True, slots=True)
class RAGResult:
    answer: str
    sources: tuple[ContextSource, ...]
    retrieved_chunks: int
    model: str | None
    search_mode: str = "hybrid"
    evidence_level: str = LEVEL_INSUFFICIENT
    evidence_score: float = 0.0
    evidence_signals: dict = field(default_factory=dict)
    conflicts: tuple[Conflict, ...] = ()
    source_groups: tuple[EvidenceGroup, ...] = ()
    insufficient_evidence: bool = False
    retrieval_ms: int = 0
    rerank_ms: int = 0
    llm_ms: int = 0
    total_ms: int = 0
    conversation_id: UUID | None = None
    message_id: UUID | None = None


def _resolve_search_mode(value: str | None) -> str:
    settings = get_settings()
    mode = (value or settings.rag_search_mode_default or "hybrid").strip().lower()
    if mode not in VALID_SEARCH_MODES:
        raise BadRequestError("search_mode must be semantic, keyword or hybrid")
    return mode


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
    """Audit without ever storing question text, chunk text or prompts."""
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
                semantic_similarity=candidate.semantic_similarity,
                keyword_score=candidate.keyword_score,
                fused_score=candidate.fused_score,
                retrieval_sources=candidate.retrieval_sources,
            )
        )
    return result


def _record_error_metrics(
    context: AuthenticatedContext,
    settings,
    *,
    query_id: UUID,
    search_mode: str,
    request_started: float,
    error_code: str,
    candidate_count: int = 0,
    authorized_count: int = 0,
    denied_count: int = 0,
    retrieval_ms: int = 0,
) -> None:
    if not settings.rag_analytics_enabled:
        return
    record_retrieval_metrics(
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        query_id=query_id,
        search_mode=search_mode,
        candidate_count=candidate_count,
        authorized_count=authorized_count,
        denied_count=denied_count,
        retrieval_ms=retrieval_ms,
        total_ms=int((time.monotonic() - request_started) * 1000),
        citation_valid=False,
        evidence_level=LEVEL_INSUFFICIENT,
        insufficient_evidence=True,
        retrieval_error=True,
        error_code=error_code,
    )


def _persist_turn(
    factory: SessionFactory,
    context: AuthenticatedContext,
    conversation_id: UUID,
    question: str,
    result: RAGResult,
    settings,
) -> tuple[UUID, UUID] | None:
    """Persist one turn. Never stores retrieved evidence text."""
    try:
        with factory() as session:
            with session.begin():
                set_tenant_context(session, context.tenant_id)
                conversation = require_conversation(
                    session, context, conversation_id
                )
                if conversation.title is None:
                    conversation.title = build_title(
                        question,
                        max_chars=settings.rag_conversation_title_max_chars,
                    )
                add_message(
                    session,
                    conversation,
                    role="user",
                    content=question,
                )
                assistant = add_message(
                    session,
                    conversation,
                    role="assistant",
                    content=result.answer,
                    citations=citations_payload(result.sources),
                    evidence_level=result.evidence_level,
                )
                history = recent_messages(
                    session,
                    context,
                    conversation.conversation_id,
                    limit=settings.rag_conversation_summary_turns,
                )
                summarize_conversation(
                    session,
                    conversation,
                    history,
                    summary_turns=settings.rag_conversation_summary_turns,
                )
                return conversation.conversation_id, assistant.message_id
    except Exception:
        # A persistence failure must not turn a validated answer into an error.
        logger.exception("Failed to persist conversation turn")
        return None


def _finalize(
    *,
    context: AuthenticatedContext,
    settings,
    factory: SessionFactory,
    query_id: UUID,
    conversation_id: UUID | None,
    question: str,
    result: RAGResult,
    request_started: float,
    candidate_count: int,
    authorized_count: int,
    denied_count: int,
    retrieval_error: bool = False,
    error_code: str | None = None,
) -> RAGResult:
    total_ms = int((time.monotonic() - request_started) * 1000)
    stored_conversation_id = conversation_id
    message_id: UUID | None = None

    if conversation_id is not None:
        persisted = _persist_turn(
            factory, context, conversation_id, question, result, settings
        )
        if persisted is not None:
            stored_conversation_id, message_id = persisted

    final = replace(
        result,
        total_ms=total_ms,
        conversation_id=stored_conversation_id,
        message_id=message_id,
    )

    if settings.rag_analytics_enabled:
        record_retrieval_metrics(
            tenant_id=context.tenant_id,
            user_id=context.user.user_id,
            query_id=query_id,
            search_mode=final.search_mode,
            candidate_count=candidate_count,
            authorized_count=authorized_count,
            denied_count=denied_count,
            retrieval_ms=final.retrieval_ms,
            rerank_ms=final.rerank_ms,
            llm_ms=final.llm_ms,
            total_ms=final.total_ms,
            citation_valid=final.retrieved_chunks > 0
            and not final.insufficient_evidence,
            evidence_level=final.evidence_level,
            insufficient_evidence=final.insufficient_evidence,
            retrieval_error=retrieval_error,
            error_code=error_code,
        )
    return final


def _insufficient_result(
    *,
    answer: str,
    mode: str,
    retrieval_ms: int,
    rerank_ms: int,
    llm_ms: int,
    model: str | None,
    assessment,
) -> RAGResult:
    return RAGResult(
        answer=answer,
        sources=(),
        retrieved_chunks=0,
        model=model,
        search_mode=mode,
        evidence_level=LEVEL_INSUFFICIENT,
        evidence_score=assessment.score if assessment is not None else 0.0,
        evidence_signals=assessment.signals if assessment is not None else {},
        insufficient_evidence=True,
        retrieval_ms=retrieval_ms,
        rerank_ms=rerank_ms,
        llm_ms=llm_ms,
    )


def answer_question(
    context: AuthenticatedContext,
    question: str,
    *,
    top_k: int,
    search_mode: str | None = None,
    filters: RAGFilters | None = None,
    conversation_id: UUID | None = None,
    request_meta: RequestMeta | None = None,
    session_factory: SessionFactory | None = None,
    embedding_service: EmbeddingService | None = None,
    llm_service: OllamaLLMService | None = None,
    reranker: SupportsRerank | None = None,
) -> RAGResult:
    settings = get_settings()
    factory = session_factory or SessionLocal
    query_id = uuid4()
    request_started = time.monotonic()

    if not context.user.is_active:
        raise ForbiddenError("User is inactive")
    if not context.organization.is_active:
        raise ForbiddenError("Organization is inactive")

    validate_top_k(top_k)
    mode = _resolve_search_mode(search_mode)
    embedder = embedding_service or get_embedding_service()

    candidate_count = 0
    authorized_count = 0
    denied_count = 0
    retrieval_ms = 0
    rerank_ms = 0
    llm_ms = 0

    # ------------------------------------------------------------------
    # 1. Conversation ownership + topic (never evidence)
    # ------------------------------------------------------------------
    topic: str | None = None
    if conversation_id is not None:
        with factory() as session:
            with session.begin():
                set_tenant_context(session, context.tenant_id)
                conversation = require_conversation(
                    session, context, conversation_id
                )
                history = recent_messages(
                    session,
                    context,
                    conversation.conversation_id,
                    limit=settings.rag_conversation_history_turns,
                )
                topic = conversation_topic(history, conversation.summary)

    # ------------------------------------------------------------------
    # 2. Query understanding / rewriting / expansion / decomposition
    # ------------------------------------------------------------------
    plan = build_query_plan(
        question,
        conversation_topic=topic,
        max_expansions=settings.rag_query_max_expansions,
        max_subqueries=settings.rag_query_max_subqueries,
        enable_understanding=settings.rag_query_understanding_enabled,
        enable_expansion=settings.rag_query_expansion_enabled,
        enable_decomposition=settings.rag_query_decomposition_enabled,
        max_chars=settings.rag_query_max_chars,
    )

    # ------------------------------------------------------------------
    # 3. Query embeddings (original question first, bounded variants)
    # ------------------------------------------------------------------
    embedding_started = time.monotonic()
    try:
        base = generate_query_embedding(
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
        _record_error_metrics(
            context,
            settings,
            query_id=query_id,
            search_mode=mode,
            request_started=request_started,
            error_code="EMBEDDING_FAILURE",
        )
        raise

    logger.info(
        "RAG stage=query_embedding query_id=%s duration_ms=%d mode=%s",
        query_id,
        int((time.monotonic() - embedding_started) * 1000),
        mode,
    )

    variant_texts: list[str] = []
    for candidate in (question, *plan.retrieval_queries):
        text = " ".join((candidate or "").split())[: settings.rag_query_max_chars]
        if not text:
            continue
        try:
            text = validate_question(text, max_chars=settings.rag_query_max_chars)
        except BadRequestError:
            continue
        if any(text.lower() == existing.lower() for existing in variant_texts):
            continue
        variant_texts.append(text)
        if len(variant_texts) >= MAX_QUERY_VARIANTS:
            break
    if not variant_texts:
        variant_texts = [question.strip()]

    extra_vectors: dict[str, list[float]] = {}
    if mode != "keyword" and len(variant_texts) > 1:
        try:
            vectors = embedder.generate_embeddings(variant_texts[1:])
            if len(vectors) == len(variant_texts) - 1:
                for text, vector in zip(variant_texts[1:], vectors):
                    if len(vector) != embedder.dimension:
                        continue
                    if any(not math.isfinite(float(value)) for value in vector):
                        continue
                    extra_vectors[text] = [float(value) for value in vector]
        except Exception:
            # Expansion is an optimization. It must never fail the request or
            # widen retrieval; degrade to the original query.
            logger.warning(
                "Query variant embedding failed; using the original query only"
            )

    semantic_variants: list[tuple[str, list[float]]] = []
    if mode in {"semantic", "hybrid"}:
        semantic_variants.append((variant_texts[0], base.vector))
        for text in variant_texts[1:]:
            vector = extra_vectors.get(text)
            if vector is not None:
                semantic_variants.append((text, vector))

    keyword_variants = (
        variant_texts[:MAX_KEYWORD_VARIANTS]
        if mode in {"keyword", "hybrid"}
        else []
    )

    try:
        _audit(
            context,
            request_meta,
            resource_id=query_id,
            action="RAG_QUERY_START",
            decision="ALLOW",
            reason=(
                f"mode={mode};variants={len(variant_texts)};"
                f"subquestions={len(plan.subquestions)}"
            ),
            policy=POLICY_ALLOW,
        )
    except Exception:
        logger.exception("Failed to audit RAG query start")

    # ------------------------------------------------------------------
    # 4. Retrieval + current authorization + authorized text
    # ------------------------------------------------------------------
    authorized: list[AuthorizedChunk] = []
    retrieval_started = time.monotonic()
    try:
        with factory() as session:
            with session.begin():
                set_tenant_context(session, context.tenant_id)
                auth_context = load_retrieval_authorization_context(
                    session, context
                )

                semantic_lists = [
                    retrieve_semantic_candidates(
                        session,
                        context,
                        vector,
                        top_k=settings.rag_semantic_candidates,
                        model_label=base.model_label,
                        role_ids=auth_context.role_ids,
                        filters=filters,
                    )
                    for _text, vector in semantic_variants
                ]
                keyword_lists = [
                    retrieve_keyword_candidates(
                        session,
                        context,
                        text,
                        top_k=settings.rag_keyword_candidates,
                        model_label=base.model_label,
                        role_ids=auth_context.role_ids,
                        filters=filters,
                    )
                    for text in keyword_variants
                ]

                candidates = fuse_candidate_lists(
                    semantic_lists=semantic_lists,
                    keyword_lists=keyword_lists,
                    rrf_k=settings.rag_rrf_k,
                    limit=settings.rag_hybrid_candidates,
                )
                candidate_count = len(candidates)
                filter_result = filter_authorized_chunks(
                    session,
                    context,
                    candidates,
                    model_label=base.model_label,
                    authorization_context=auth_context,
                    request_meta=request_meta,
                )
                authorized = _authorized_chunks_with_text(
                    session, context, filter_result.authorized
                )
                authorized_count = len(filter_result.authorized)
                denied_count = len(filter_result.denied)
                _audit(
                    context,
                    request_meta,
                    resource_id=query_id,
                    action="RAG_RETRIEVAL",
                    decision="ALLOW",
                    reason=(
                        f"mode={mode};candidates={candidate_count};"
                        f"authorized={authorized_count};denied={denied_count}"
                    ),
                    policy=POLICY_ALLOW,
                    session=session,
                )
    except Exception as exc:
        if isinstance(
            exc, (BadRequestError, ForbiddenError, ServiceUnavailableError)
        ):
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
        _record_error_metrics(
            context,
            settings,
            query_id=query_id,
            search_mode=mode,
            request_started=request_started,
            error_code="RETRIEVAL_FAILURE",
        )
        raise ServiceUnavailableError("Permission-aware retrieval failed") from exc

    retrieval_ms = int((time.monotonic() - retrieval_started) * 1000)
    logger.info(
        "RAG stage=retrieval_authorization query_id=%s duration_ms=%d mode=%s "
        "candidates=%d authorized=%d denied=%d",
        query_id,
        retrieval_ms,
        mode,
        candidate_count,
        authorized_count,
        denied_count,
    )

    if not authorized:
        assessment = assess_evidence(
            ranked=(),
            sources=(),
            citation_coverage=0.0,
            invalid_citations=0,
            conflict_count=0,
            min_coverage=settings.rag_citation_min_coverage,
            strong_threshold=settings.rag_evidence_strong_threshold,
            moderate_threshold=settings.rag_evidence_moderate_threshold,
            limited_threshold=settings.rag_evidence_limited_threshold,
        )
        return _finalize(
            context=context,
            settings=settings,
            factory=factory,
            query_id=query_id,
            conversation_id=conversation_id,
            question=question,
            result=_insufficient_result(
                answer=NO_CONTEXT_ANSWER,
                mode=mode,
                retrieval_ms=retrieval_ms,
                rerank_ms=0,
                llm_ms=0,
                model=None,
                assessment=assessment,
            ),
            request_started=request_started,
            candidate_count=candidate_count,
            authorized_count=authorized_count,
            denied_count=denied_count,
        )

    # ------------------------------------------------------------------
    # 5. Rerank, merge and diversify authorized evidence
    # ------------------------------------------------------------------
    reranker_impl = reranker
    if reranker_impl is None and settings.rag_rerank_enabled:
        reranker_impl = CrossEncoderReranker(settings.rag_reranker_model)

    rerank_started = time.monotonic()
    ranked = rerank_evidence(
        authorized,
        limit=settings.rag_context_top_n,
        question=plan.rewritten or question,
        reranker=reranker_impl,
        min_similarity=settings.rag_min_similarity,
        max_candidates=settings.rag_rerank_candidates,
        per_document_limit=settings.rag_max_chunks_per_document,
        merge_adjacent=settings.rag_merge_adjacent_chunks,
        rerank_min_score=settings.rag_rerank_min_score,
    )
    rerank_ms = int((time.monotonic() - rerank_started) * 1000)
    logger.info(
        "RAG stage=rerank query_id=%s duration_ms=%d mode=%s authorized=%d selected=%d",
        query_id,
        rerank_ms,
        mode,
        len(authorized),
        len(ranked),
    )

    if not ranked:
        assessment = assess_evidence(
            ranked=(),
            sources=(),
            citation_coverage=0.0,
            invalid_citations=0,
            conflict_count=0,
            min_coverage=settings.rag_citation_min_coverage,
            strong_threshold=settings.rag_evidence_strong_threshold,
            moderate_threshold=settings.rag_evidence_moderate_threshold,
            limited_threshold=settings.rag_evidence_limited_threshold,
        )
        return _finalize(
            context=context,
            settings=settings,
            factory=factory,
            query_id=query_id,
            conversation_id=conversation_id,
            question=question,
            result=_insufficient_result(
                answer=NO_CONTEXT_ANSWER,
                mode=mode,
                retrieval_ms=retrieval_ms,
                rerank_ms=rerank_ms,
                llm_ms=0,
                model=None,
                assessment=assessment,
            ),
            request_started=request_started,
            candidate_count=candidate_count,
            authorized_count=authorized_count,
            denied_count=denied_count,
        )

    # ------------------------------------------------------------------
    # 6. Bounded context + evidence grouping + conflict detection
    # ------------------------------------------------------------------
    counter = getattr(embedder, "count_tokens", None)
    built = build_context(
        ranked,
        max_tokens=settings.rag_max_context_tokens,
        token_counter=counter,
    )
    if not built.sources:
        assessment = assess_evidence(
            ranked=ranked,
            sources=(),
            citation_coverage=0.0,
            invalid_citations=0,
            conflict_count=0,
            min_coverage=settings.rag_citation_min_coverage,
            strong_threshold=settings.rag_evidence_strong_threshold,
            moderate_threshold=settings.rag_evidence_moderate_threshold,
            limited_threshold=settings.rag_evidence_limited_threshold,
        )
        return _finalize(
            context=context,
            settings=settings,
            factory=factory,
            query_id=query_id,
            conversation_id=conversation_id,
            question=question,
            result=_insufficient_result(
                answer=NO_CONTEXT_ANSWER,
                mode=mode,
                retrieval_ms=retrieval_ms,
                rerank_ms=rerank_ms,
                llm_ms=0,
                model=None,
                assessment=assessment,
            ),
            request_started=request_started,
            candidate_count=candidate_count,
            authorized_count=authorized_count,
            denied_count=denied_count,
        )

    evidence_items = build_evidence_items(built.sources, ranked)
    evidence_groups = group_evidence(evidence_items)
    conflicts = detect_conflicts(built.sources)

    try:
        _audit(
            context,
            request_meta,
            resource_id=query_id,
            action="RAG_LLM_START",
            decision="ALLOW",
            reason=(
                f"mode={mode};context_sources={len(built.sources)};"
                f"documents={len(evidence_groups)};conflicts={len(conflicts)}"
            ),
            policy=POLICY_ALLOW,
        )
    except Exception:
        logger.exception("Failed to audit LLM start")

    # ------------------------------------------------------------------
    # 7. Local generation
    # ------------------------------------------------------------------
    instructions = reasoning_instructions(
        plan.analysis,
        conflict_count=len(conflicts),
        distinct_documents=len(evidence_groups),
    )

    llm = llm_service or get_llm_service()
    llm_started = time.monotonic()
    try:
        if instructions and hasattr(llm, "generate_messages"):
            prompt = build_prompt(
                question,
                built.context,
                extra_instructions=instructions,
            )
            llm_response = llm.generate_messages(prompt)
        else:
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
        _record_error_metrics(
            context,
            settings,
            query_id=query_id,
            search_mode=mode,
            request_started=request_started,
            error_code="LLM_FAILURE",
            candidate_count=candidate_count,
            authorized_count=authorized_count,
            denied_count=denied_count,
            retrieval_ms=retrieval_ms,
        )
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
        _record_error_metrics(
            context,
            settings,
            query_id=query_id,
            search_mode=mode,
            request_started=request_started,
            error_code="LLM_FAILURE",
            candidate_count=candidate_count,
            authorized_count=authorized_count,
            denied_count=denied_count,
            retrieval_ms=retrieval_ms,
        )
        raise ServiceUnavailableError(
            "The local language model is unavailable"
        ) from exc

    llm_ms = int((time.monotonic() - llm_started) * 1000)

    # ------------------------------------------------------------------
    # 8. Citation + claim validation
    # ------------------------------------------------------------------
    citation_started = time.monotonic()
    audit = audit_answer(llm_response.answer, built.sources)
    citation = audit.validation
    citation_ms = int((time.monotonic() - citation_started) * 1000)
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
                f"coverage={audit.coverage};latency_ms={llm_ms}"
            ),
            policy="CITATION_VALIDATION_POLICY",
        )
    except Exception:
        logger.exception("Failed to audit citation validation")

    if not citation.has_valid_citation:
        return _finalize(
            context=context,
            settings=settings,
            factory=factory,
            query_id=query_id,
            conversation_id=conversation_id,
            question=question,
            result=RAGResult(
                answer=UNVERIFIABLE_ANSWER,
                sources=(),
                retrieved_chunks=0,
                model=llm_response.model,
                search_mode=mode,
                evidence_level=LEVEL_INSUFFICIENT,
                evidence_score=0.0,
                evidence_signals={
                    "source_count": len(built.sources),
                    "invalid_citations": len(citation.invalid_source_ids),
                    "citation_coverage": audit.coverage,
                },
                insufficient_evidence=True,
                retrieval_ms=retrieval_ms,
                rerank_ms=rerank_ms,
                llm_ms=llm_ms,
            ),
            request_started=request_started,
            candidate_count=candidate_count,
            authorized_count=authorized_count,
            denied_count=denied_count,
        )

    # ------------------------------------------------------------------
    # 9. Post-generation re-authorization (closes the LLM window)
    # ------------------------------------------------------------------
    with factory() as session:
        with session.begin():
            set_tenant_context(session, context.tenant_id)
            allowed_sources, revoked = reauthorize_sources(
                session, context, citation.sources
            )

    if revoked:
        try:
            _audit(
                context,
                request_meta,
                resource_id=query_id,
                action="RAG_CITATION_VALIDATION",
                decision="DENY",
                reason=(
                    "Authorization changed during generation; "
                    f"revoked_documents={len(revoked)}"
                ),
                policy="PERMISSION_VALIDITY_POLICY",
            )
        except Exception:
            logger.exception("Failed to audit post-generation revocation")
        return _finalize(
            context=context,
            settings=settings,
            factory=factory,
            query_id=query_id,
            conversation_id=conversation_id,
            question=question,
            result=RAGResult(
                answer=UNVERIFIABLE_ANSWER,
                sources=(),
                retrieved_chunks=0,
                model=llm_response.model,
                search_mode=mode,
                evidence_level=LEVEL_INSUFFICIENT,
                evidence_score=0.0,
                evidence_signals={
                    "revoked_documents": len(revoked),
                    "source_count": len(citation.sources),
                },
                insufficient_evidence=True,
                retrieval_ms=retrieval_ms,
                rerank_ms=rerank_ms,
                llm_ms=llm_ms,
            ),
            request_started=request_started,
            candidate_count=candidate_count,
            authorized_count=authorized_count,
            denied_count=denied_count,
        )

    # ------------------------------------------------------------------
    # 10. Evidence quality + insufficient-evidence handling
    # ------------------------------------------------------------------
    assessment = assess_evidence(
        ranked=ranked,
        sources=allowed_sources,
        citation_coverage=audit.coverage,
        invalid_citations=len(citation.invalid_source_ids),
        conflict_count=len(conflicts),
        min_coverage=settings.rag_citation_min_coverage,
        strong_threshold=settings.rag_evidence_strong_threshold,
        moderate_threshold=settings.rag_evidence_moderate_threshold,
        limited_threshold=settings.rag_evidence_limited_threshold,
    )

    if not assessment.sufficient:
        try:
            _audit(
                context,
                request_meta,
                resource_id=query_id,
                action="RAG_INSUFFICIENT_EVIDENCE",
                decision="DENY",
                reason=(
                    f"level={assessment.level};score={assessment.score};"
                    f"coverage={audit.coverage}"
                ),
                policy="EVIDENCE_QUALITY_POLICY",
            )
        except Exception:
            logger.exception("Failed to audit insufficient evidence")
        return _finalize(
            context=context,
            settings=settings,
            factory=factory,
            query_id=query_id,
            conversation_id=conversation_id,
            question=question,
            result=_insufficient_result(
                answer=INSUFFICIENT_EVIDENCE_ANSWER,
                mode=mode,
                retrieval_ms=retrieval_ms,
                rerank_ms=rerank_ms,
                llm_ms=llm_ms,
                model=llm_response.model,
                assessment=assessment,
            ),
            request_started=request_started,
            candidate_count=candidate_count,
            authorized_count=authorized_count,
            denied_count=denied_count,
        )

    final_items = build_evidence_items(allowed_sources, ranked)
    final_groups = group_evidence(final_items)

    logger.info(
        "RAG stage=complete query_id=%s total_ms=%d embedding_ms=%d "
        "retrieval_authorization_ms=%d rerank_ms=%d context_build_ms=%d "
        "llm_ms=%d citation_validation_ms=%d mode=%s candidates=%d "
        "authorized=%d denied=%d sources=%d evidence=%s",
        query_id,
        int((time.monotonic() - request_started) * 1000),
        int((time.monotonic() - embedding_started) * 1000),
        retrieval_ms,
        rerank_ms,
        int((time.monotonic() - citation_started) * 1000),
        llm_ms,
        citation_ms,
        mode,
        candidate_count,
        authorized_count,
        denied_count,
        len(allowed_sources),
        assessment.level,
    )

    try:
        _audit(
            context,
            request_meta,
            resource_id=query_id,
            action="RAG_LLM_COMPLETE",
            decision="ALLOW",
            reason=(
                f"mode={mode};latency_ms={llm_ms};sources={len(allowed_sources)};"
                f"evidence={assessment.level}"
            ),
            policy=POLICY_ALLOW,
        )
    except Exception:
        logger.exception("Failed to audit LLM completion")

    result = RAGResult(
        answer=citation.answer or UNVERIFIABLE_ANSWER,
        sources=allowed_sources,
        retrieved_chunks=len(allowed_sources),
        model=llm_response.model,
        search_mode=mode,
        evidence_level=assessment.level,
        evidence_score=assessment.score,
        evidence_signals=assessment.signals,
        conflicts=conflicts,
        source_groups=final_groups,
        insufficient_evidence=False,
        retrieval_ms=retrieval_ms,
        rerank_ms=rerank_ms,
        llm_ms=llm_ms,
    )

    return _finalize(
        context=context,
        settings=settings,
        factory=factory,
        query_id=query_id,
        conversation_id=conversation_id,
        question=question,
        result=result,
        request_started=request_started,
        candidate_count=candidate_count,
        authorized_count=authorized_count,
        denied_count=denied_count,
    )
