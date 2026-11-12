"""Safe PDF validation and page-level text extraction with PyMuPDF.

This module never invents text and never logs extracted content. Encrypted,
malformed and image-only PDFs fail with stable, safe error codes so the
ingestion service can persist a useful status without leaking internals.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from app.core.security_constants import (
    ERROR_ENCRYPTED_PDF,
    ERROR_MALFORMED_PDF,
    ERROR_NO_EXTRACTABLE_TEXT,
    ERROR_OCR_UNSUPPORTED,
    ERROR_PDF_EXTRACTION_FAILED,
    ERROR_PDF_PAGE_LIMIT_EXCEEDED,
)

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")


class DocumentProcessingError(Exception):
    """Base class for safe PDF processing failures."""

    code = ERROR_PDF_EXTRACTION_FAILED
    default_message = "The PDF could not be processed"

    def __init__(self, message: str | None = None) -> None:
        self.code = type(self).code
        self.safe_message = message or self.default_message
        super().__init__(self.safe_message)


class InvalidPdfError(DocumentProcessingError):
    code = ERROR_MALFORMED_PDF
    default_message = "The uploaded file is not a valid PDF"


class MalformedPdfError(DocumentProcessingError):
    code = ERROR_MALFORMED_PDF
    default_message = "The PDF is malformed or unreadable"


class EncryptedPdfError(DocumentProcessingError):
    code = ERROR_ENCRYPTED_PDF
    default_message = "Encrypted or password-protected PDFs are not supported"


class NoExtractableTextError(DocumentProcessingError):
    code = ERROR_NO_EXTRACTABLE_TEXT
    default_message = "The PDF contains no extractable text"


class OcrUnsupportedError(NoExtractableTextError):
    code = ERROR_OCR_UNSUPPORTED
    default_message = (
        "No extractable text was found. OCR is not supported in this milestone."
    )


class PdfPageLimitError(DocumentProcessingError):
    code = ERROR_PDF_PAGE_LIMIT_EXCEEDED
    default_message = "The PDF exceeds the maximum supported page count"


@dataclass(frozen=True, slots=True)
class PdfValidationResult:
    page_count: int
    is_pdf: bool
    encrypted: bool


@dataclass(frozen=True, slots=True)
class PdfPage:
    page_number: int
    text: str


@dataclass(frozen=True, slots=True)
class PdfExtractionResult:
    pages: tuple[PdfPage, ...]
    total_pages: int
    total_extractable_chars: int
    empty_page_numbers: tuple[int, ...]


def _open_pdf(path: str | Path) -> fitz.Document:
    file_path = Path(path)
    if not file_path.is_file():
        raise MalformedPdfError("The PDF file is missing")
    try:
        document = fitz.open(str(file_path), filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several exception classes
        raise MalformedPdfError("The uploaded file is not a valid PDF") from exc

    try:
        if not document.is_pdf:
            raise InvalidPdfError("The uploaded file is not a PDF")
        if document.needs_pass or document.is_encrypted:
            raise EncryptedPdfError()
        if document.page_count < 1:
            raise MalformedPdfError("The PDF contains no pages")
        # Force one page object to load so a broken page tree fails before we
        # accept the upload.
        document.load_page(0)
        return document
    except Exception:
        document.close()
        raise


def validate_pdf(path: str | Path) -> PdfValidationResult:
    """Open and validate a PDF without extracting or logging its text."""
    document = _open_pdf(path)
    try:
        return PdfValidationResult(
            page_count=document.page_count,
            is_pdf=True,
            encrypted=False,
        )
    finally:
        document.close()


def extract_pdf_text(
    path: str | Path,
    *,
    min_extractable_chars: int = 50,
    max_pages: int | None = None,
) -> PdfExtractionResult:
    """Extract text page by page, preserving page numbers and empty pages."""
    document = _open_pdf(path)
    pages: list[PdfPage] = []
    empty_pages: list[int] = []
    total_chars = 0
    try:
        if max_pages is not None and document.page_count > max_pages:
            raise PdfPageLimitError()

        for index in range(document.page_count):
            page_number = index + 1
            try:
                page = document.load_page(index)
                text = page.get_text("text", sort=True)
            except Exception as exc:
                raise PdfExtractionError(
                    f"Text extraction failed on page {page_number}"
                ) from exc

            pages.append(PdfPage(page_number=page_number, text=text))
            if not text.strip():
                empty_pages.append(page_number)
            total_chars += len(_WHITESPACE.sub("", text))

        if total_chars < min_extractable_chars:
            logger.info(
                "PDF has insufficient extractable text pages=%s chars=%s",
                document.page_count,
                total_chars,
            )
            raise OcrUnsupportedError()

        return PdfExtractionResult(
            pages=tuple(pages),
            total_pages=document.page_count,
            total_extractable_chars=total_chars,
            empty_page_numbers=tuple(empty_pages),
        )
    finally:
        document.close()


class PdfExtractionError(DocumentProcessingError):
    code = ERROR_PDF_EXTRACTION_FAILED
    default_message = "PDF text extraction failed"
