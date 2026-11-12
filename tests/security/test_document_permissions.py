"""Document authorization freshness and revocation attacks."""
from app.tests.test_rag_security import (
    test_expired_document_permission_excludes_chunk,
    test_revocation_after_ingestion_denies_without_reembedding,
)
from app.tests.test_permission_revocation import test_cross_tenant_revocation_is_not_found

__all__ = [
    "test_expired_document_permission_excludes_chunk",
    "test_revocation_after_ingestion_denies_without_reembedding",
    "test_cross_tenant_revocation_is_not_found",
]
