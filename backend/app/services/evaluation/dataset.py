"""Evaluation dataset import, validation and synthetic corpus materialization.

Datasets reference documents by name and chunks by ``(document_name,
chunk_index)``. The runner resolves those references against the tenant's
current corpus, so a re-ingested document keeps its evaluation labels while the
underlying chunk IDs change.

The bundled synthetic corpus is entirely fictional. Materializing it uses the
normal upload and ingestion pipeline, including permission assignment and
embedding, so evaluation exercises the same code path as a real document.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import fitz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import set_tenant_context
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.request_context import RequestMeta
from app.core.security_constants import DOC_STATUS_COMPLETED
from app.dependencies.authentication import AuthenticatedContext
from app.models import (
    Document,
    DocumentChunk,
    EvaluationCase,
    EvaluationDataset,
)
from app.services.document_upload import upload_document
from app.services.ingestion_service import process_document

DATA_DIR = Path(__file__).resolve().parent / "data"
SYNTHETIC_DATASET_FILE = DATA_DIR / "synthetic_enterprise_v1.json"
MAX_CASES = 200
MAX_DOCUMENTS = 50


def load_synthetic_payload() -> dict[str, Any]:
    with SYNTHETIC_DATASET_FILE.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an imported dataset shape before any row is written."""
    from app.models.evaluation import EVALUATION_CATEGORIES

    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 200:
        raise BadRequestError("Dataset name is required and must be at most 200 characters")
    version = int(payload.get("version") or 1)
    if version < 1:
        raise BadRequestError("Dataset version must be at least 1")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise BadRequestError("A dataset must contain at least one case")
    if len(cases) > MAX_CASES:
        raise BadRequestError(f"A dataset may contain at most {MAX_CASES} cases")

    documents = payload.get("documents") or []
    if not isinstance(documents, list) or len(documents) > MAX_DOCUMENTS:
        raise BadRequestError(
            f"Dataset documents must be a list of at most {MAX_DOCUMENTS} entries"
        )

    seen_keys: set[str] = set()
    normalized_cases: list[dict[str, Any]] = []
    for index, raw in enumerate(cases, start=1):
        if not isinstance(raw, dict):
            raise BadRequestError(f"Case {index} must be an object")
        case_key = str(raw.get("key") or "").strip()
        if not case_key or len(case_key) > 80:
            raise BadRequestError(f"Case {index} needs a key of at most 80 characters")
        if case_key in seen_keys:
            raise BadRequestError(f"Duplicate case key: {case_key}")
        seen_keys.add(case_key)
        category = str(raw.get("category") or "").strip()
        if category not in EVALUATION_CATEGORIES:
            raise BadRequestError(f"Case {case_key} has an unknown category")
        question = str(raw.get("question") or "").strip()
        if not question or len(question) > 4000:
            raise BadRequestError(f"Case {case_key} needs a question of at most 4000 characters")

        def names(field: str) -> list[str]:
            value = raw.get(field) or []
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not item.strip() for item in value
            ):
                raise BadRequestError(f"Case {case_key} field {field} must be a list of names")
            return [item.strip() for item in value]

        gold_chunks = raw.get("gold_chunks") or []
        if not isinstance(gold_chunks, list):
            raise BadRequestError(f"Case {case_key} gold_chunks must be a list")
        normalized_chunks: list[dict[str, Any]] = []
        for chunk in gold_chunks:
            if not isinstance(chunk, dict):
                raise BadRequestError(f"Case {case_key} has a malformed gold chunk")
            document_name = str(chunk.get("document_name") or "").strip()
            chunk_index = chunk.get("chunk_index")
            if not document_name or not isinstance(chunk_index, int) or chunk_index < 0:
                raise BadRequestError(f"Case {case_key} has a malformed gold chunk")
            normalized_chunks.append(
                {"document_name": document_name, "chunk_index": chunk_index}
            )

        filters = raw.get("filters") or {}
        if not isinstance(filters, dict):
            raise BadRequestError(f"Case {case_key} filters must be an object")

        normalized_cases.append(
            {
                "key": case_key,
                "category": category,
                "question": question,
                "expected_answer": (
                    str(raw["expected_answer"]).strip()
                    if raw.get("expected_answer") is not None
                    else None
                ),
                "expected_answerable": bool(raw.get("expected_answerable", True)),
                "allowed_document_names": names("allowed_document_names"),
                "gold_document_names": names("gold_document_names"),
                "gold_chunks": normalized_chunks,
                "expected_citation_document_names": names(
                    "expected_citation_document_names"
                ),
                "restricted_document_names": names("restricted_document_names"),
                "filters": {str(key): str(value) for key, value in filters.items()},
                "notes": (
                    str(raw["notes"])[:2000] if raw.get("notes") is not None else None
                ),
            }
        )

    normalized_documents: list[dict[str, str]] = []
    for document in documents:
        if not isinstance(document, dict):
            raise BadRequestError("Each dataset document must be an object")
        document_name = str(document.get("name") or "").strip()
        text = str(document.get("text") or "").strip()
        if not document_name or len(document_name) > 255 or not text:
            raise BadRequestError(
                "Each dataset document needs a name (<=255 chars) and text"
            )
        normalized_documents.append(
            {
                "name": document_name,
                "department": str(document.get("department") or "HR").strip().upper(),
                "classification": str(
                    document.get("classification") or "INTERNAL"
                ).strip().upper(),
                "text": text,
            }
        )

    return {
        "name": name,
        "version": version,
        "description": (
            str(payload["description"])[:4000]
            if payload.get("description") is not None
            else None
        ),
        "source": (
            "SYNTHETIC" if payload.get("source") == "SYNTHETIC" else "IMPORTED"
        ),
        "documents": normalized_documents,
        "cases": normalized_cases,
    }


def import_dataset(
    db: Session,
    context: AuthenticatedContext,
    payload: dict[str, Any],
) -> EvaluationDataset:
    """Persist one validated dataset version for the caller's tenant."""
    normalized = validate_payload(payload)
    set_tenant_context(db, context.tenant_id)

    existing = db.scalar(
        select(EvaluationDataset).where(
            EvaluationDataset.tenant_id == context.tenant_id,
            EvaluationDataset.name == normalized["name"],
            EvaluationDataset.version == normalized["version"],
        )
    )
    if existing is not None:
        raise ConflictError(
            "A dataset with this name and version already exists; import a new version"
        )

    dataset = EvaluationDataset(
        tenant_id=context.tenant_id,
        name=normalized["name"],
        version=normalized["version"],
        description=normalized["description"],
        source=normalized["source"],
        query_count=len(normalized["cases"]),
        created_by=context.user.user_id,
    )
    db.add(dataset)
    db.flush()

    for case in normalized["cases"]:
        db.add(
            EvaluationCase(
                tenant_id=context.tenant_id,
                dataset_id=dataset.dataset_id,
                case_key=case["key"],
                category=case["category"],
                question=case["question"],
                expected_answer=case["expected_answer"],
                expected_answerable=case["expected_answerable"],
                allowed_document_names=case["allowed_document_names"],
                gold_document_names=case["gold_document_names"],
                gold_chunks=case["gold_chunks"],
                expected_citation_document_names=case[
                    "expected_citation_document_names"
                ],
                restricted_document_names=case["restricted_document_names"],
                filters=case["filters"],
                notes=case["notes"],
            )
        )
    db.commit()
    set_tenant_context(db, context.tenant_id)
    db.refresh(dataset)
    return dataset


def get_dataset(
    db: Session, *, tenant_id: UUID, dataset_id: UUID
) -> EvaluationDataset:
    dataset = db.scalar(
        select(EvaluationDataset).where(
            EvaluationDataset.tenant_id == tenant_id,
            EvaluationDataset.dataset_id == dataset_id,
        )
    )
    if dataset is None:
        raise NotFoundError("Evaluation dataset not found")
    return dataset


def list_dataset_cases(db: Session, dataset: EvaluationDataset) -> list[EvaluationCase]:
    return list(
        db.scalars(
            select(EvaluationCase)
            .where(
                EvaluationCase.tenant_id == dataset.tenant_id,
                EvaluationCase.dataset_id == dataset.dataset_id,
            )
            .order_by(EvaluationCase.case_key)
        ).all()
    )


def resolve_document_names(
    db: Session, *, tenant_id: UUID, names: list[str]
) -> tuple[dict[str, UUID], list[str]]:
    """Map names to current tenant documents; return (resolved, missing)."""
    if not names:
        return {}, []
    rows = db.execute(
        select(Document.document_name, Document.document_id).where(
            Document.tenant_id == tenant_id,
            Document.document_name.in_(names),
            Document.is_active.is_(True),
        )
    ).all()
    mapping = {name: document_id for name, document_id in rows}
    missing = [name for name in names if name not in mapping]
    return mapping, missing


def resolve_gold_chunks(
    db: Session,
    *,
    tenant_id: UUID,
    document_ids: dict[str, UUID],
    gold_chunks: list[dict[str, Any]],
) -> tuple[list[UUID], list[dict[str, Any]]]:
    """Resolve ``(document_name, chunk_index)`` labels to live chunk IDs."""
    resolved: list[UUID] = []
    missing: list[dict[str, Any]] = []
    for item in gold_chunks:
        document_id = document_ids.get(str(item.get("document_name")))
        index = item.get("chunk_index")
        if document_id is None or not isinstance(index, int):
            missing.append(item)
            continue
        chunk_id = db.scalar(
            select(DocumentChunk.chunk_id).where(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.document_id == document_id,
                DocumentChunk.chunk_index == index,
            )
        )
        if chunk_id is None:
            missing.append(item)
        else:
            resolved.append(chunk_id)
    return resolved, missing


def _pdf_bytes(text: str) -> bytes:
    """Render synthetic plain text into a small PDF for the normal pipeline."""
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(
        fitz.Rect(48, 48, 548, 780),
        text,
        fontsize=10,
        fontname="helv",
    )
    data = document.tobytes()
    document.close()
    return data


def materialize_synthetic_documents(
    db: Session,
    context: AuthenticatedContext,
    *,
    request_meta: RequestMeta,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create and ingest the bundled synthetic corpus in the caller's tenant.

    Existing completed documents with the same name are reused. Documents with
    the same content under a different name cause a safe conflict through the
    normal duplicate-detection path; nothing is silently overwritten.
    """
    data = payload or load_synthetic_payload()
    documents = data.get("documents") or []
    set_tenant_context(db, context.tenant_id)

    created: list[str] = []
    reused: list[str] = []
    for entry in documents:
        name = str(entry["name"])
        existing = db.scalar(
            select(Document).where(
                Document.tenant_id == context.tenant_id,
                Document.document_name == name,
            )
        )
        if existing is not None and existing.processing_status == DOC_STATUS_COMPLETED:
            reused.append(name)
            continue
        if existing is not None:
            # A prior partial run left an uploaded/failed row. Do not overwrite
            # it; surface a safe conflict instead.
            raise ConflictError(
                f"Synthetic document '{name}' exists but is not fully processed"
            )

        pdf = _pdf_bytes(str(entry["text"]))
        document = upload_document(
            db,
            context,
            stream=io.BytesIO(pdf),
            original_filename=f"{name}.pdf",
            content_type="application/pdf",
            document_name=name,
            department=str(entry.get("department") or "HR"),
            classification=str(entry.get("classification") or "INTERNAL"),
            request_meta=request_meta,
        )
        set_tenant_context(db, context.tenant_id)
        process_document(
            context,
            document.document_id,
            request_meta=request_meta,
        )
        set_tenant_context(db, context.tenant_id)
        created.append(name)

    return {"created": created, "reused": reused, "total": len(created) + len(reused)}


def synthesize_dataset_payload() -> dict[str, Any]:
    """The bundled payload with the correct source label for import."""
    payload = load_synthetic_payload()
    payload["source"] = "SYNTHETIC"
    return payload
