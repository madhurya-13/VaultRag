"""PDF validation and page-level extraction tests using real PyMuPDF files."""

import pytest

from app.services.document_processor import (
    EncryptedPdfError,
    MalformedPdfError,
    OcrUnsupportedError,
    extract_pdf_text,
    validate_pdf,
)
from app.tests.helpers import DEFAULT_PDF_TEXT, make_blank_pdf_bytes, make_pdf_bytes


def _write(tmp_path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_validate_pdf_accepts_real_pdf(tmp_path):
    path = _write(tmp_path, "valid.pdf", make_pdf_bytes())
    result = validate_pdf(path)

    assert result.is_pdf is True
    assert result.encrypted is False
    assert result.page_count == 1


def test_extract_pdf_text_preserves_pages_and_empty_pages(tmp_path):
    data = make_pdf_bytes([DEFAULT_PDF_TEXT, ""])
    path = _write(tmp_path, "two-pages.pdf", data)

    result = extract_pdf_text(path)

    assert result.total_pages == 2
    assert [page.page_number for page in result.pages] == [1, 2]
    assert result.pages[0].text.strip()
    assert result.pages[1].text.strip() == ""
    assert result.empty_page_numbers == (2,)
    assert result.total_extractable_chars > 50


def test_extract_pdf_text_rejects_renamed_non_pdf(tmp_path):
    path = _write(tmp_path, "renamed.pdf", b"this is definitely not a PDF")

    with pytest.raises(MalformedPdfError):
        validate_pdf(path)


def test_extract_pdf_text_rejects_encrypted_pdf(tmp_path):
    path = _write(
        tmp_path,
        "encrypted.pdf",
        make_pdf_bytes(encrypt_password="correct horse battery staple"),
    )

    with pytest.raises(EncryptedPdfError):
        validate_pdf(path)
    with pytest.raises(EncryptedPdfError):
        extract_pdf_text(path)


def test_extract_pdf_text_rejects_image_only_pdf(tmp_path):
    path = _write(tmp_path, "image-only.pdf", make_pdf_bytes(image_only=True))

    with pytest.raises(OcrUnsupportedError) as error:
        extract_pdf_text(path)

    assert error.value.code == "OCR_UNSUPPORTED"


def test_extract_pdf_text_rejects_blank_pdf(tmp_path):
    path = _write(tmp_path, "blank.pdf", make_blank_pdf_bytes())

    with pytest.raises(OcrUnsupportedError):
        extract_pdf_text(path)


def test_extract_pdf_text_rejects_missing_file(tmp_path):
    with pytest.raises(MalformedPdfError):
        extract_pdf_text(tmp_path / "missing.pdf")


def test_extract_pdf_text_rejects_truncated_pdf(tmp_path):
    path = _write(
        tmp_path,
        "truncated.pdf",
        b"%PDF-1.4\n" + b"\x00" * 200,
    )

    with pytest.raises(MalformedPdfError):
        validate_pdf(path)
