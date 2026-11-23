"""Execute a labeled dataset through VaultRAG's real permission-aware pipeline.

Security properties
--------------------
* Runs execute as a real tenant user (an ADMIN by default, or an explicitly
  selected ``viewer_user_id`` in the same tenant). There is no evaluation-only
  authorization bypass and no synthetic "all documents" mode.
* Retrieval uses the same ``answer_question`` path as production, including
  current permission checks, RLS and model-label filtering.
* Every retrieved document is independently re-authorized after the run. Any
  denial, or any restricted dataset document appearing in results, is recorded
  as an authorization violation and fails the case.

Deterministic metrics are computed locally. The optional judge is separate and
never required.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal, set_tenant_context
from app.core.exceptions import BadRequestError, NotFoundError, ServiceUnavailableError
from app.core.request_context import RequestMeta
from app.dependencies.authentication import (
    AuthenticatedContext,
    load_authenticated_context,
)
from app.models import (
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
)
from app.schemas.rag import RAGFilters
from app.services.authorization import authorize_document
from app.services.embedding_service import EmbeddingService
from app.services.evaluation.dataset import (
    get_dataset,
    list_dataset_cases,
    resolve_document_names,
    resolve_gold_chunks,
)
from app.services.evaluation.judge import JudgeResult, judge_answer
from app.services.evaluation.metrics import (
    aggregate,
    answer_metrics,
    retrieval_metrics,
)
from app.services.llm_service import OllamaLLMService
from app.services.rag.retrieval_trace import RetrievalTrace
from app.services.rag_service import answer_question
from app.services.reranking import CrossEncoderReranker

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]

CONFIGURATIONS: dict[str, dict[str, Any]] = {
    "semantic": {"mode": "semantic", "rerank": False},
    "keyword": {"mode": "keyword", "rerank": False},
    "hybrid": {"mode": "hybrid", "rerank": False},
    "hybrid_rerank": {"mode": "hybrid", "rerank": True},
    "hybrid_graph": {"mode": "graph_hybrid", "rerank": False},
    "hybrid_graph_rerank": {"mode": "graph_hybrid", "rerank": True},
}

K_VALUES = (3, 5, 10)


class LexicalOverlapReranker:
    """Deterministic reranking proxy used when the cross-encoder is disabled.

    It is labeled as a proxy in run metrics. It measures whether reranking
    improves this dataset under the same evidence, without downloading a model.
    """

    implementation = "lexical_proxy"

    def score(self, question: str, texts: list[str]) -> list[float]:
        query = set(re.findall(r"[a-z0-9]+", question.lower()))
        if not query:
            return [0.0 for _ in texts]
        return [
            len(query & set(re.findall(r"[a-z0-9]+", text.lower()))) / len(query)
            for text in texts
        ]


def _resolve_reranker(configuration: str):
    if not CONFIGURATIONS[configuration]["rerank"]:
        return None, None
    settings = get_settings()
    if settings.rag_rerank_enabled:
        return CrossEncoderReranker(settings.rag_reranker_model), "cross_encoder"
    return LexicalOverlapReranker(), LexicalOverlapReranker.implementation


def _as_str(value: UUID | str) -> str:
    return str(value)


def _filters_for(case: EvaluationCase) -> RAGFilters | None:
    if not case.filters:
        return None
    try:
        return RAGFilters(**case.filters)
    except Exception as exc:
        raise BadRequestError(f"Case {case.case_key} has invalid filters") from exc


def _update_run(
    factory: SessionFactory,
    *,
    tenant_id: UUID,
    run_id: UUID,
    **values,
) -> None:
    with factory() as session:
        with session.begin():
            set_tenant_context(session, tenant_id)
            run = session.scalar(
                select(EvaluationRun).where(
                    EvaluationRun.tenant_id == tenant_id,
                    EvaluationRun.run_id == run_id,
                )
            )
            if run is None:
                return
            for key, value in values.items():
                setattr(run, key, value)


def _persist_result(
    factory: SessionFactory,
    *,
    tenant_id: UUID,
    run_id: UUID,
    case_id: UUID,
    metrics: dict[str, Any],
    answer: str | None,
    citations: list[dict],
    retrieved_chunk_ids: list[str],
    retrieved_document_ids: list[str],
    passed: bool,
    error_code: str | None,
    latency_ms: int,
) -> None:
    with factory() as session:
        with session.begin():
            set_tenant_context(session, tenant_id)
            session.add(
                EvaluationResult(
                    result_id=uuid4(),
                    tenant_id=tenant_id,
                    run_id=run_id,
                    case_id=case_id,
                    retrieved_chunk_ids=retrieved_chunk_ids,
                    retrieved_document_ids=retrieved_document_ids,
                    answer=answer,
                    citations=citations,
                    metrics=metrics,
                    passed=passed,
                    error_code=error_code,
                    latency_ms=max(0, int(latency_ms)),
                )
            )


def _authorization_violations(
    factory: SessionFactory,
    *,
    context: AuthenticatedContext,
    document_ids: list[str],
    restricted_ids: set[str],
) -> list[str]:
    """Re-check every returned document with the same central engine."""
    denied: list[str] = []
    with factory() as session:
        with session.begin():
            set_tenant_context(session, context.tenant_id)
            for value in document_ids:
                try:
                    document_id = UUID(value)
                except ValueError:
                    denied.append(value)
                    continue
                if value in restricted_ids or not authorize_document(
                    session, context, document_id, "READ"
                ).allowed:
                    denied.append(value)
    return denied


def _case_passed(
    *,
    expected_answerable: bool,
    gold_chunk_ids: list[str],
    retrieved_chunk_ids: list[str],
    generated: bool,
    insufficient: bool,
    citation_valid: bool | None,
    violations: int,
    error_code: str | None,
) -> bool:
    if error_code or violations:
        return False
    if expected_answerable:
        if gold_chunk_ids and not set(gold_chunk_ids) & set(retrieved_chunk_ids):
            return False
        if generated and not citation_valid:
            return False
        return True
    # An unanswerable or restricted case passes only by abstaining. Retrieval
    # mode cannot observe generation-time abstention, so it passes when no
    # unauthorized/restricted evidence was returned; it is a retrieval check,
    # not an answerability claim.
    if generated:
        return bool(insufficient)
    return True


def execute_run(
    db: Session,
    context: AuthenticatedContext,
    *,
    dataset_id: UUID,
    configuration: str,
    top_k: int,
    answer_mode: str,
    judge_enabled: bool,
    viewer_user_id: UUID | None,
    request_meta: RequestMeta,
    session_factory: SessionFactory | None = None,
    embedding_service: EmbeddingService | None = None,
    llm_service: OllamaLLMService | None = None,
) -> EvaluationRun:
    """Create and synchronously execute one evaluation run."""
    factory = session_factory or SessionLocal
    if configuration not in CONFIGURATIONS:
        raise BadRequestError("Unknown evaluation configuration")
    if answer_mode not in {"retrieval", "generated"}:
        raise BadRequestError("answer_mode must be retrieval or generated")
    if not 1 <= top_k <= 50:
        raise BadRequestError("top_k must be between 1 and 50")

    run_context = context
    if viewer_user_id is not None:
        loaded = load_authenticated_context(
            db, tenant_id=context.tenant_id, user_id=viewer_user_id
        )
        if loaded is None:
            raise BadRequestError("The selected viewer is not an active tenant user")
        run_context = loaded

    dataset = get_dataset(db, tenant_id=context.tenant_id, dataset_id=dataset_id)
    cases = list_dataset_cases(db, dataset)
    if not cases:
        raise BadRequestError("The dataset contains no cases")

    reranker, reranker_implementation = _resolve_reranker(configuration)
    started = datetime.now(timezone.utc)
    run_id = uuid4()
    with factory() as session:
        with session.begin():
            set_tenant_context(session, context.tenant_id)
            session.add(
                EvaluationRun(
                    run_id=run_id,
                    tenant_id=context.tenant_id,
                    dataset_id=dataset.dataset_id,
                    configuration=configuration,
                    top_k=top_k,
                    answer_mode=answer_mode,
                    judge_enabled=judge_enabled,
                    viewer_user_id=viewer_user_id,
                    status="RUNNING",
                    case_count=len(cases),
                    created_by=context.user.user_id,
                    started_at=started,
                )
            )
            session.flush()
            # Load the persisted run so the returned object carries DB defaults.
            run = session.get(EvaluationRun, run_id)

    rows: list[dict[str, Any]] = []
    judge_rows: list[JudgeResult] = []
    completed = 0
    failed = 0
    violations_total = 0

    allowed_names = sorted(
        {
            name
            for case in cases
            for name in (case.allowed_document_names or [])
        }
    )
    restricted_names = sorted(
        {
            name
            for case in cases
            for name in (case.restricted_document_names or [])
        }
    )
    with factory() as session:
        with session.begin():
            set_tenant_context(session, context.tenant_id)
            allowed_docs, _ = resolve_document_names(
                session, tenant_id=context.tenant_id, names=allowed_names
            )
            restricted_docs, _ = resolve_document_names(
                session, tenant_id=context.tenant_id, names=restricted_names
            )
    restricted_ids = {str(value) for value in restricted_docs.values()}

    for case in cases:
        case_started = time.monotonic()
        error_code: str | None = None
        retrieved_chunk_ids: list[str] = []
        retrieved_document_ids: list[str] = []
        metrics: dict[str, Any] = {}
        answer = None
        citations: list[dict] = []
        passed = False
        insufficient = True
        citation_valid: bool | None = None
        violations = 0

        try:
            with factory() as session:
                with session.begin():
                    set_tenant_context(session, context.tenant_id)
                    gold_docs, missing_gold_docs = resolve_document_names(
                        session,
                        tenant_id=context.tenant_id,
                        names=case.gold_document_names or [],
                    )
                    gold_chunks, missing_chunks = resolve_gold_chunks(
                        session,
                        tenant_id=context.tenant_id,
                        document_ids=gold_docs,
                        gold_chunks=case.gold_chunks or [],
                    )
                    allowed, missing_allowed = resolve_document_names(
                        session,
                        tenant_id=context.tenant_id,
                        names=case.allowed_document_names or [],
                    )

            if missing_allowed or missing_gold_docs or missing_chunks:
                raise FileNotFoundError("Dataset documents or chunks are not available")

            result = answer_question(
                run_context,
                case.question,
                top_k=top_k,
                search_mode=CONFIGURATIONS[configuration]["mode"],
                filters=_filters_for(case),
                request_meta=request_meta,
                session_factory=factory,
                embedding_service=embedding_service,
                llm_service=llm_service,
                reranker=reranker,
                trace_limit=max(10, top_k),
                retrieval_only=answer_mode == "retrieval",
            )
            trace: RetrievalTrace | None = result.trace
            retrieved_chunk_ids = (
                [str(value) for value in trace.ranked_chunk_ids] if trace else []
            )
            retrieved_document_ids = (
                [str(value) for value in trace.ranked_document_ids] if trace else []
            )
            insufficient = result.insufficient_evidence
            answer = result.answer if answer_mode == "generated" else None

            violations_list = _authorization_violations(
                factory,
                context=run_context,
                document_ids=retrieved_document_ids,
                restricted_ids=restricted_ids,
            )
            violations = len(violations_list)

            metrics = retrieval_metrics(
                gold_chunk_ids=[str(value) for value in gold_chunks],
                gold_document_ids=[str(value) for value in gold_docs.values()],
                retrieved_chunk_ids=retrieved_chunk_ids,
                retrieved_document_ids=retrieved_document_ids,
                k_values=K_VALUES,
            )
            metrics["authorization_violations"] = violations
            metrics["missing_gold_chunks"] = len(missing_chunks)

            if answer_mode == "generated":
                evidence = [source.text for source in result.sources]
                valid_ids = {source.source_id for source in result.sources}
                citations = [
                    {
                        "domain_source_id": source.source_id,
                        "document_id": str(source.document_id),
                        "document_name": source.document_name,
                        "chunk_id": str(source.chunk_id),
                        "page_start": source.page_start,
                        "page_end": source.page_end,
                    }
                    for source in result.sources
                ]
                metrics.update(
                    answer_metrics(
                        answer=result.answer,
                        expected_answer=case.expected_answer,
                        evidence_texts=evidence,
                        valid_source_ids=valid_ids,
                        answerable_expected=case.expected_answerable,
                        abstained=result.insufficient_evidence,
                    )
                )
                citation_valid = bool(metrics.get("citation_validity"))
                if judge_enabled:
                    judged = judge_answer(
                        question=case.question,
                        answer=result.answer,
                        reference_answer=case.expected_answer,
                        evidence=evidence,
                    )
                    if judged is not None:
                        metrics["judge"] = {
                            "relevance": judged.relevance,
                            "correctness": judged.correctness,
                            "faithfulness": judged.faithfulness,
                            "prompt_version": judged.prompt_version,
                        }
                        judge_rows.append(judged)

            passed = _case_passed(
                expected_answerable=case.expected_answerable,
                gold_chunk_ids=[str(value) for value in gold_chunks],
                retrieved_chunk_ids=retrieved_chunk_ids,
                generated=answer_mode == "generated",
                insufficient=insufficient,
                citation_valid=citation_valid,
                violations=violations,
                error_code=None,
            )
            metrics["passed"] = passed
        except FileNotFoundError:
            error_code = "MISSING_DOCUMENTS"
        except ServiceUnavailableError:
            error_code = "RAG_UNAVAILABLE"
        except BadRequestError:
            error_code = "INVALID_CASE"
        except Exception:
            logger.exception("Evaluation case failed case_key=%s", case.case_key)
            error_code = "CASE_FAILURE"

        latency_ms = int((time.monotonic() - case_started) * 1000)
        if error_code is not None:
            failed += 1
            metrics["passed"] = False
            metrics["authorization_violations"] = violations
        else:
            completed += 1
        violations_total += violations
        metrics["error_code"] = error_code
        metrics["latency_ms"] = latency_ms
        rows.append(metrics)

        _persist_result(
            factory,
            tenant_id=context.tenant_id,
            run_id=run_id,
            case_id=case.case_id,
            metrics=metrics,
            answer=answer,
            citations=citations,
            retrieved_chunk_ids=retrieved_chunk_ids,
            retrieved_document_ids=retrieved_document_ids,
            passed=passed,
            error_code=error_code,
            latency_ms=latency_ms,
        )
        _update_run(
            factory,
            tenant_id=context.tenant_id,
            run_id=run_id,
            completed_cases=completed,
            failed_cases=failed,
            authorization_violations=violations_total,
        )

    summary = aggregate(rows, K_VALUES)
    summary.update(
        {
            "configuration": configuration,
            "answer_mode": answer_mode,
            "top_k": top_k,
            "reranker_implementation": reranker_implementation,
            "judge_enabled": judge_enabled,
            "judge_prompt_version": (
                judge_rows[0].prompt_version if judge_rows else None
            ),
            "judge_model": get_settings().ollama_model if judge_rows else None,
        }
    )
    if judge_rows:
        for key in ("relevance", "correctness", "faithfulness"):
            values = [
                getattr(row, key)
                for row in judge_rows
                if getattr(row, key) is not None
            ]
            if values:
                summary[f"judge_{key}"] = round(sum(values) / len(values), 4)

    with factory() as session:
        with session.begin():
            set_tenant_context(session, context.tenant_id)
            run = session.scalar(
                select(EvaluationRun).where(
                    EvaluationRun.tenant_id == context.tenant_id,
                    EvaluationRun.run_id == run_id,
                )
            )
            if run is None:
                raise NotFoundError("Evaluation run disappeared")
            run.metrics = summary
            run.status = "COMPLETED"
            run.completed_at = datetime.now(timezone.utc)
            session.flush()
            run_id = run.run_id

    with factory() as session:
        with session.begin():
            set_tenant_context(session, context.tenant_id)
            run = session.scalar(
                select(EvaluationRun).where(
                    EvaluationRun.tenant_id == context.tenant_id,
                    EvaluationRun.run_id == run_id,
                )
            )
            session.expunge(run)
    return run
