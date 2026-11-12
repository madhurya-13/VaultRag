"""Malicious and malformed document upload attack simulations."""
from app.tests.test_document_upload import (
    test_local_storage_enforces_size_limit_and_path_traversal,
    test_upload_rejects_encrypted_pdf,
    test_upload_rejects_image_only_pdf,
    test_upload_rejects_malformed_pdf,
    test_upload_rejects_non_pdf_extension,
    test_upload_rejects_pdf_over_page_limit_without_registering_document,
    test_upload_rejects_renamed_non_pdf_content,
    test_upload_rejects_wrong_declared_content_type,
)
from app.tests.test_ingestion_security import test_cross_tenant_ingestion_access_is_not_found

__all__ = [
    "test_local_storage_enforces_size_limit_and_path_traversal",
    "test_upload_rejects_encrypted_pdf",
    "test_upload_rejects_image_only_pdf",
    "test_upload_rejects_malformed_pdf",
    "test_upload_rejects_non_pdf_extension",
    "test_upload_rejects_pdf_over_page_limit_without_registering_document",
    "test_upload_rejects_renamed_non_pdf_content",
    "test_upload_rejects_wrong_declared_content_type",
    "test_cross_tenant_ingestion_access_is_not_found",
]
