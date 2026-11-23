"""VaultRAG FastAPI application entry point.

Run with:
    uvicorn app.main:app --reload

Interactive docs: http://127.0.0.1:8000/docs
"""

import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import get_settings
from app.core.database import engine, get_db
from app.core.exceptions import AppError
from app.routes import (
    analytics,
    attributes,
    auth,
    documents,
    evaluation,
    feedback,
    integrations,
    knowledge_gaps,
    knowledge_graph,
    organizations,
    rag,
    roles,
    security,
    tenant_settings,
    users,
)

settings = get_settings()


class StructuredJsonFormatter(logging.Formatter):
    """Emit compact structured production logs without request/document data."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
if settings.environment == "production":
    for handler in logging.getLogger().handlers:
        handler.setFormatter(StructuredJsonFormatter())
logger = logging.getLogger("vaultrag")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Fail fast if PostgreSQL is unreachable instead of serving broken requests."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    logger.info("VaultRAG starting (environment=%s)", settings.environment)
    yield
    engine.dispose()
    logger.info("VaultRAG stopped")


app = FastAPI(
    title="VaultRAG API",
    version="1.0.0",
    summary="Permission-aware multi-tenant RAG platform - Milestone 10",
    description=(
        "Secure RAG backend: tenants, users, tenant-scoped roles, JWT "
        "authentication, centralized RBAC/ABAC document authorization, "
        "PostgreSQL row-level security, permission-aware pgvector retrieval, "
        "local Ollama generation, validated citations and append-only audit "
        "logs.\n\n"
        "**Security guarantee:** unauthorized document chunk text is never "
        "selected for context construction or sent to the language model.\n\n"
        "**Authentication:** call `POST /auth/login` with email, password and "
        "tenant_id, then click **Authorize** and paste the returned "
        "`access_token`."
    ),
    lifespan=lifespan,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc",
    openapi_url=None if settings.environment == "production" else "/openapi.json",
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.allowed_hosts,
)

# Only explicitly configured origins may call the API from a browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    """Translate domain errors to safe JSON. Details are intentional and safe."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=exc.headers,
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Conservative response headers. No API response should be cached."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    if request.url.path not in {"/docs", "/redoc"}:
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.environment == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Attach a correlation ID and client IP to the request/audit context.

    A caller-supplied X-Request-ID is accepted only as a correlation hint, never
    for authorization. Invalid values are replaced with a fresh UUID.
    """
    raw_request_id = request.headers.get("X-Request-ID")
    request_id: UUID
    if raw_request_id:
        try:
            request_id = UUID(raw_request_id)
        except ValueError:
            request_id = uuid4()
    else:
        request_id = uuid4()

    request.state.request_id = request_id
    request.state.client_ip = request.client.host if request.client else None
    request.state.started_at = time.monotonic()

    response = await call_next(request)
    response.headers["X-Request-ID"] = str(request_id)
    logger.info(
        "request method=%s path=%s status=%d duration_ms=%d request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        int((time.monotonic() - request.state.started_at) * 1000),
        request_id,
    )
    return response


@app.get("/", tags=["Health"], summary="Service metadata")
def root() -> dict[str, str | None]:
    return {
        "service": "VaultRAG API",
        "version": app.version,
        "docs": None if settings.environment == "production" else "/docs",
    }


@app.get("/health", tags=["Health"], summary="Database-backed health check")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable"}


@app.get("/health/live", tags=["Health"], summary="Process liveness")
def health_live() -> dict[str, str]:
    """Process-only liveness. Never touches the database or external services."""
    return {"status": "ok", "probe": "live"}


@app.get("/health/ready", tags=["Health"], summary="Dependency readiness")
def health_ready(db: Session = Depends(get_db)) -> JSONResponse:
    """Readiness without secrets or content."""
    components: dict[str, str] = {}
    ready = True
    try:
        db.execute(text("SELECT 1"))
        components["database"] = "ready"
    except Exception:
        components["database"] = "unavailable"
        ready = False
    try:
        vector = db.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one_or_none()
        components["pgvector"] = "ready" if vector else "unavailable"
        ready = ready and bool(vector)
    except Exception:
        components["pgvector"] = "unavailable"
        ready = False
    try:
        tsv = db.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'document_chunks' AND column_name = 'content_tsv'"
            )
        ).scalar_one_or_none()
        components["full_text_search"] = "ready" if tsv else "unavailable"
        ready = ready and bool(tsv)
    except Exception:
        components["full_text_search"] = "unavailable"
        ready = False
    components["embedding_model"] = settings.embedding_model_name
    components["ollama"] = "optional"
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "degraded", "components": components},
    )


app.include_router(auth.router)
app.include_router(organizations.router)
app.include_router(users.router)
app.include_router(roles.router)
app.include_router(attributes.router)
app.include_router(documents.router)
app.include_router(integrations.oauth_router)
app.include_router(integrations.router)
app.include_router(integrations.versions_router)
app.include_router(knowledge_graph.router)
app.include_router(evaluation.router)
app.include_router(feedback.router)
app.include_router(feedback.admin_router)
app.include_router(knowledge_gaps.router)
app.include_router(tenant_settings.router)
app.include_router(analytics.router)
app.include_router(rag.router)
app.include_router(security.router)
