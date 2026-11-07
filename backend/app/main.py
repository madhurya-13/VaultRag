"""VaultRAG FastAPI application entry point.

Run with:
    uvicorn app.main:app --reload

Interactive docs: http://127.0.0.1:8000/docs
"""

import logging
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import engine, get_db
from app.core.exceptions import AppError
from app.routes import attributes, auth, documents, organizations, security, users

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
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
    version="2.0.0",
    summary="Permission-aware multi-tenant RAG platform - Milestone 2 authorization",
    description=(
        "Secure backend foundation: tenants, users, tenant-scoped roles, JWT "
        "authentication, centralized RBAC/ABAC document authorization, "
        "PostgreSQL row-level security and append-only security audit logs.\n\n"
        "**Authentication:** call `POST /auth/login` with email, password and "
        "tenant_id, then click **Authorize** and paste the returned "
        "`access_token`."
    ),
    lifespan=lifespan,
)

# Only explicitly configured origins may call the API from a browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
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

    response = await call_next(request)
    response.headers["X-Request-ID"] = str(request_id)
    return response


@app.get("/", tags=["Health"], summary="Service metadata")
def root() -> dict[str, str]:
    return {
        "service": "VaultRAG API",
        "version": app.version,
        "environment": settings.environment,
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"], summary="Database-backed health check")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable"}


app.include_router(auth.router)
app.include_router(organizations.router)
app.include_router(users.router)
app.include_router(attributes.router)
app.include_router(documents.router)
app.include_router(security.router)
