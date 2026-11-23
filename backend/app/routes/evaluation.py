"""Administrator-only RAG evaluation API.

All endpoints are tenant-scoped by the authenticated context and require the
``MANAGE_EVALUATION`` capability. Runs execute through the real permission-aware
pipeline, optionally as a selected tenant viewer.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.core.request_context import RequestMeta, get_request_meta
from app.core.security_constants import CAP_MANAGE_EVALUATION, POLICY_ALLOW
from app.dependencies.authentication import (
    AuthenticatedContext,
    require_capability,
)
from app.models import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationResult,
    EvaluationRun,
)
from app.schemas.evaluation import (
    DatasetImportRequest,
    EvaluationCaseResponse,
    EvaluationConfigurationsResponse,
    EvaluationConfigurationResponse,
    EvaluationDatasetDetailResponse,
    EvaluationDatasetListResponse,
    EvaluationDatasetResponse,
    EvaluationMetricsResponse,
    EvaluationResultListResponse,
    EvaluationResultResponse,
    EvaluationRunListResponse,
    EvaluationRunRequest,
    EvaluationRunResponse,
    SyntheticDatasetRequest,
)
from app.services.evaluation.dataset import (
    get_dataset,
    import_dataset,
    list_dataset_cases,
    materialize_synthetic_documents,
    synthesize_dataset_payload,
)
from app.services.evaluation.runner import CONFIGURATIONS, execute_run
from app.services.security_audit import log_security_event

router = APIRouter(prefix="/evaluation", tags=["RAG Evaluation"])

admin_only = require_capability(CAP_MANAGE_EVALUATION)


def _dataset_response(dataset: EvaluationDataset) -> EvaluationDatasetResponse:
    return EvaluationDatasetResponse(
        dataset_id=dataset.dataset_id,
        name=dataset.name,
        version=dataset.version,
        description=dataset.description,
        source=dataset.source,
        query_count=dataset.query_count,
        created_by=dataset.created_by,
        created_at=dataset.created_at,
    )


def _case_response(case: EvaluationCase) -> EvaluationCaseResponse:
    return EvaluationCaseResponse(
        case_id=case.case_id,
        case_key=case.case_key,
        category=case.category,
        question=case.question,
        expected_answer=case.expected_answer,
        expected_answerable=case.expected_answerable,
        allowed_document_names=list(case.allowed_document_names or []),
        gold_document_names=list(case.gold_document_names or []),
        gold_chunks=list(case.gold_chunks or []),
        expected_citation_document_names=list(
            case.expected_citation_document_names or []
        ),
        restricted_document_names=list(case.restricted_document_names or []),
        filters=dict(case.filters or {}),
        notes=case.notes,
    )


def _run_response(
    run: EvaluationRun, dataset_name: str | None = None
) -> EvaluationRunResponse:
    return EvaluationRunResponse(
        run_id=run.run_id,
        dataset_id=run.dataset_id,
        dataset_name=dataset_name,
        configuration=run.configuration,
        top_k=run.top_k,
        answer_mode=run.answer_mode,
        judge_enabled=run.judge_enabled,
        viewer_user_id=run.viewer_user_id,
        status=run.status,
        case_count=run.case_count,
        completed_cases=run.completed_cases,
        failed_cases=run.failed_cases,
        authorization_violations=run.authorization_violations,
        metrics=dict(run.metrics or {}),
        error_code=run.error_code,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
    )


def _audit(
    db: Session,
    context: AuthenticatedContext,
    request_meta: RequestMeta,
    *,
    resource_id: UUID,
    action: str,
    reason: str,
) -> None:
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="EVALUATION",
        resource_id=resource_id,
        action=action,
        decision="ALLOW",
        reason=reason,
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )


@router.get(
    "/configurations",
    response_model=EvaluationConfigurationsResponse,
    summary="Available retrieval configurations",
)
def configurations(
    _: AuthenticatedContext = Depends(admin_only),
) -> EvaluationConfigurationsResponse:
    descriptions = {
        "semantic": "pgvector semantic retrieval only.",
        "keyword": "PostgreSQL full-text keyword retrieval only.",
        "hybrid": "Semantic + keyword fused with reciprocal rank fusion.",
        "hybrid_rerank": "Hybrid plus the local reranker (cross-encoder if enabled, deterministic proxy otherwise).",
        "hybrid_graph": "Hybrid plus knowledge-graph evidence.",
        "hybrid_graph_rerank": "Hybrid + graph evidence, then reranking.",
    }
    return EvaluationConfigurationsResponse(
        configurations=[
            EvaluationConfigurationResponse(
                configuration=name,
                search_mode=config["mode"],
                rerank=bool(config["rerank"]),
                graph=config["mode"] == "graph_hybrid",
                description=descriptions[name],
            )
            for name, config in CONFIGURATIONS.items()
        ]
    )


@router.get(
    "/datasets",
    response_model=EvaluationDatasetListResponse,
    summary="List tenant evaluation datasets",
)
def list_datasets(
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> EvaluationDatasetListResponse:
    rows = db.scalars(
        select(EvaluationDataset)
        .where(EvaluationDataset.tenant_id == context.tenant_id)
        .order_by(EvaluationDataset.created_at.desc())
    ).all()
    return EvaluationDatasetListResponse(
        datasets=[_dataset_response(row) for row in rows]
    )


@router.post(
    "/datasets",
    response_model=EvaluationDatasetDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import a labeled evaluation dataset",
)
def create_dataset(
    payload: DatasetImportRequest,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> EvaluationDatasetDetailResponse:
    dataset = import_dataset(db, context, payload.model_dump())
    _audit(
        db,
        context,
        request_meta,
        resource_id=dataset.dataset_id,
        action="DATASET_IMPORT",
        reason=f"cases={dataset.query_count};source={dataset.source}",
    )
    return EvaluationDatasetDetailResponse(
        **_dataset_response(dataset).model_dump(),
        cases=[_case_response(case) for case in list_dataset_cases(db, dataset)],
    )


@router.post(
    "/datasets/synthetic",
    response_model=EvaluationDatasetDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import the bundled synthetic dataset and optionally ingest its corpus",
)
def create_synthetic_dataset(
    payload: SyntheticDatasetRequest,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> EvaluationDatasetDetailResponse:
    if payload.create_documents:
        materialize_synthetic_documents(
            db, context, request_meta=request_meta
        )
    dataset = import_dataset(db, context, synthesize_dataset_payload())
    _audit(
        db,
        context,
        request_meta,
        resource_id=dataset.dataset_id,
        action="DATASET_IMPORT",
        reason=(
            f"cases={dataset.query_count};source={dataset.source};"
            f"documents_created={payload.create_documents}"
        ),
    )
    return EvaluationDatasetDetailResponse(
        **_dataset_response(dataset).model_dump(),
        cases=[_case_response(case) for case in list_dataset_cases(db, dataset)],
    )


@router.get(
    "/datasets/{dataset_id}",
    response_model=EvaluationDatasetDetailResponse,
    summary="Read one dataset and its cases",
)
def read_dataset(
    dataset_id: UUID,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> EvaluationDatasetDetailResponse:
    dataset = get_dataset(db, tenant_id=context.tenant_id, dataset_id=dataset_id)
    return EvaluationDatasetDetailResponse(
        **_dataset_response(dataset).model_dump(),
        cases=[_case_response(case) for case in list_dataset_cases(db, dataset)],
    )


@router.post(
    "/runs",
    response_model=EvaluationRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Execute a dataset synchronously under one configuration",
)
def create_run(
    payload: EvaluationRunRequest,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> EvaluationRunResponse:
    dataset = get_dataset(
        db, tenant_id=context.tenant_id, dataset_id=payload.dataset_id
    )
    run = execute_run(
        db,
        context,
        dataset_id=payload.dataset_id,
        configuration=payload.configuration,
        top_k=payload.top_k,
        answer_mode=payload.answer_mode,
        judge_enabled=payload.judge_enabled,
        viewer_user_id=payload.viewer_user_id,
        request_meta=request_meta,
    )
    _audit(
        db,
        context,
        request_meta,
        resource_id=run.run_id,
        action="RUN_EXECUTE",
        reason=(
            f"configuration={run.configuration};cases={run.case_count};"
            f"failed={run.failed_cases};violations={run.authorization_violations}"
        ),
    )
    return _run_response(run, dataset.name)


@router.get(
    "/runs",
    response_model=EvaluationRunListResponse,
    summary="List evaluation runs for the tenant",
)
def list_runs(
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> EvaluationRunListResponse:
    rows = db.scalars(
        select(EvaluationRun)
        .where(EvaluationRun.tenant_id == context.tenant_id)
        .order_by(EvaluationRun.created_at.desc())
        .limit(limit)
    ).all()
    names = {
        dataset_id: name
        for dataset_id, name in db.execute(
            select(EvaluationDataset.dataset_id, EvaluationDataset.name).where(
                EvaluationDataset.tenant_id == context.tenant_id
            )
        ).all()
    }
    return EvaluationRunListResponse(
        runs=[_run_response(row, names.get(row.dataset_id)) for row in rows]
    )


@router.get(
    "/runs/{run_id}",
    response_model=EvaluationRunResponse,
    summary="Read one evaluation run",
)
def read_run(
    run_id: UUID,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> EvaluationRunResponse:
    run = db.scalar(
        select(EvaluationRun).where(
            EvaluationRun.tenant_id == context.tenant_id,
            EvaluationRun.run_id == run_id,
        )
    )
    if run is None:
        raise NotFoundError("Evaluation run not found")
    name = db.scalar(
        select(EvaluationDataset.name).where(
            EvaluationDataset.tenant_id == context.tenant_id,
            EvaluationDataset.dataset_id == run.dataset_id,
        )
    )
    return _run_response(run, name)


@router.get(
    "/runs/{run_id}/results",
    response_model=EvaluationResultListResponse,
    summary="Per-case results and failures for a run",
)
def read_results(
    run_id: UUID,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
    failures_only: bool = False,
) -> EvaluationResultListResponse:
    run = db.scalar(
        select(EvaluationRun).where(
            EvaluationRun.tenant_id == context.tenant_id,
            EvaluationRun.run_id == run_id,
        )
    )
    if run is None:
        raise NotFoundError("Evaluation run not found")

    statement = (
        select(EvaluationResult, EvaluationCase)
        .join(
            EvaluationCase,
            (EvaluationCase.tenant_id == EvaluationResult.tenant_id)
            & (EvaluationCase.case_id == EvaluationResult.case_id),
        )
        .where(
            EvaluationResult.tenant_id == context.tenant_id,
            EvaluationResult.run_id == run_id,
        )
        .order_by(EvaluationCase.case_key)
    )
    if failures_only:
        statement = statement.where(EvaluationResult.passed.is_(False))

    rows = db.execute(statement).all()
    return EvaluationResultListResponse(
        results=[
            EvaluationResultResponse(
                result_id=result.result_id,
                run_id=result.run_id,
                case_id=result.case_id,
                case_key=case.case_key,
                category=case.category,
                question=case.question,
                expected_answer=case.expected_answer,
                expected_answerable=case.expected_answerable,
                answer=result.answer,
                retrieved_chunk_ids=list(result.retrieved_chunk_ids or []),
                retrieved_document_ids=list(result.retrieved_document_ids or []),
                citations=list(result.citations or []),
                metrics=dict(result.metrics or {}),
                passed=result.passed,
                error_code=result.error_code,
                latency_ms=result.latency_ms,
                created_at=result.created_at,
            )
            for result, case in rows
        ]
    )


@router.get(
    "/metrics",
    response_model=EvaluationMetricsResponse,
    summary="Tenant-scoped evaluation aggregate",
)
def evaluation_metrics(
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> EvaluationMetricsResponse:
    runs = list(
        db.scalars(
            select(EvaluationRun)
            .where(EvaluationRun.tenant_id == context.tenant_id)
            .order_by(EvaluationRun.created_at.desc())
            .limit(limit)
        ).all()
    )
    names = {
        dataset_id: name
        for dataset_id, name in db.execute(
            select(EvaluationDataset.dataset_id, EvaluationDataset.name).where(
                EvaluationDataset.tenant_id == context.tenant_id
            )
        ).all()
    }
    total_cases = sum(run.case_count for run in runs)
    passed_cases = sum(
        int(run.metrics.get("cases_passed", 0) or 0) for run in runs
    )
    failed_cases = sum(
        int(run.metrics.get("cases_failed", 0) or 0) for run in runs
    )
    violations = sum(run.authorization_violations for run in runs)
    finished = [run for run in runs if run.status in {"COMPLETED", "FAILED"}]

    latencies = [
        float(run.metrics.get("average_latency_ms", 0.0))
        for run in finished
        if run.metrics.get("average_latency_ms") is not None
    ]

    by_configuration: list[dict] = []
    for configuration in CONFIGURATIONS:
        matching = [run for run in finished if run.configuration == configuration]
        if not matching:
            continue
        by_configuration.append(
            {
                "configuration": configuration,
                "runs": len(matching),
                "recall_at_5": round(
                    sum(
                        float(run.metrics.get("recall_at_5", 0.0))
                        for run in matching
                    )
                    / len(matching),
                    4,
                ),
                "mrr": round(
                    sum(float(run.metrics.get("mrr", 0.0)) for run in matching)
                    / len(matching),
                    4,
                ),
                "ndcg_at_5": round(
                    sum(float(run.metrics.get("ndcg_at_5", 0.0)) for run in matching)
                    / len(matching),
                    4,
                ),
                "citation_validity": round(
                    sum(
                        float(run.metrics.get("citation_validity", 0.0))
                        for run in matching
                    )
                    / len(matching),
                    4,
                ),
                "average_latency_ms": round(
                    sum(
                        float(run.metrics.get("average_latency_ms", 0.0))
                        for run in matching
                    )
                    / len(matching),
                    2,
                ),
            }
        )

    return EvaluationMetricsResponse(
        total_runs=len(runs),
        completed_runs=sum(1 for run in runs if run.status == "COMPLETED"),
        failed_runs=sum(1 for run in runs if run.status == "FAILED"),
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        authorization_violations=violations,
        average_latency_ms=round(sum(latencies) / len(latencies), 2)
        if latencies
        else 0.0,
        by_configuration=by_configuration,
        recent_runs=[
            _run_response(run, names.get(run.dataset_id)) for run in runs[:10]
        ],
    )
