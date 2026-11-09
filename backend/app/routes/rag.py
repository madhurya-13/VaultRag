"""Permission-aware RAG API endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.request_context import RequestMeta, get_request_meta
from app.dependencies.authentication import AuthenticatedContext, get_current_auth
from app.schemas.rag import (
    RAGAskRequest,
    RAGAskResponse,
    RAGHealthComponent,
    RAGHealthResponse,
    RAGHistoryResponse,
    RAGSource,
)
from app.services.embedding_service import get_embedding_service
from app.services.llm_service import get_llm_service
from app.services.rag_service import answer_question

router = APIRouter(prefix="/rag", tags=["Permission-Aware RAG"])


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
        request_meta=request_meta,
    )
    return RAGAskResponse(
        answer=result.answer,
        sources=[
            RAGSource(
                document_id=source.document_id,
                document_name=source.document_name,
                chunk_id=source.chunk_id,
                page_start=source.page_start,
                page_end=source.page_end,
            )
            for source in result.sources
        ],
        retrieved_chunks=result.retrieved_chunks,
        model=result.model,
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
            text(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            )
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

    overall = "ok" if all(
        component.status in {"ok", "ready", "configured"}
        for component in components.values()
    ) else "degraded"
    return RAGHealthResponse(status=overall, components=components)


@router.get(
    "/history",
    response_model=RAGHistoryResponse,
    summary="Caller-scoped RAG history (stateless initial implementation)",
)
def history(
    context: AuthenticatedContext = Depends(get_current_auth),
) -> RAGHistoryResponse:
    # No conversation persistence exists in Milestone 4. The endpoint is still
    # authenticated and has no user/tenant parameter, so it cannot be used to
    # enumerate another user's questions.
    return RAGHistoryResponse(items=[], persistent=False)
