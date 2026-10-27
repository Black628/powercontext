# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio
import hashlib
import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from powercontext.sources import SourceDefinitionRegistry, SourceMaterialization, TextEvidence
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    PDFPageSource,
    parse_pdf_document,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY

_OWNER_PASSWORD = "test-owner"  # noqa: S105 - deterministic encryption fixture
_USER_PASSWORD = "test-user"  # noqa: S105 - deterministic encryption fixture


def _document(*, password: str | None = None, broken_tail: bool = False) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 50 700 Td (Revenue 2025 1,234.56) Tj 0 -20 Td (Net income 789) Tj ET")
    page[NameObject("/Contents")] = stream
    tail = writer.add_blank_page(width=612, height=792)
    if broken_tail:
        tail[NameObject("/Contents")] = NumberObject(7)
    if password is not None:
        writer.encrypt(user_password=password, owner_password=_OWNER_PASSWORD)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_full_text_numbers_blanks_and_source_roundtrip() -> None:
    raw = _document()
    pages = parse_pdf_document(PDFDocumentInput(document_id="财报 / 2025", content=raw))
    assert len(pages) == 2
    first, blank = pages
    assert first.page_number == 1 and blank.page_number == 2 and blank.text == ""
    assert "Revenue 2025 1,234.56" in first.text and "Net income 789" in first.text
    assert all(page.document_sha256 == hashlib.sha256(raw).hexdigest() for page in pages)
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(first))
    assert isinstance(source, PDFPageSource)
    assert source.materialization is SourceMaterialization.CAPTURED
    assert PDF_PAGE_SOURCE_DEFINITION.name == "pdf-page" and PDF_PAGE_SOURCE_DEFINITION.version == "1"
    source = PDFPageSource.model_validate_json(source.model_dump_json())
    assert asyncio.run(registry.read(source)) == first
    evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
    assert evidence.source_type == "pdf-page" and evidence.source_id == source.name
    assert evidence.content == f"{first.document_id}\n{first.text}"
    assert evidence.metadata == {
        "document_id": first.document_id,
        "document_sha256": first.document_sha256,
        "page_number": 1,
    }


def test_empty_password_permission_pdf_preserves_text() -> None:
    raw = _document(password="")
    pages = parse_pdf_document(PDFDocumentInput(document_id="permissions", content=raw))
    assert len(pages) == 2 and "1,234.56" in pages[0].text


@pytest.mark.parametrize(
    "raw",
    [b"", b"not pdf", b"%PDF-1.7\ntruncated", _document(password=_USER_PASSWORD), _document(broken_tail=True)],
    ids=["empty", "not-pdf", "truncated", "password-required", "broken-last-page"],
)
def test_invalid_or_partially_readable_documents_fail_atomically(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="invalid", content=raw))


@pytest.mark.parametrize("identity", ["", " \t\n"])
def test_blank_document_identity_is_rejected(identity: str) -> None:
    with pytest.raises(ValueError):
        PDFDocumentInput(document_id=identity, content=_document())


def test_source_identity_is_bounded_stable_and_revision_specific() -> None:
    base = PDFPageInput(document_id="文档:/" * 1000, document_sha256="a" * 64, page_number=1, text="first")
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))

    def identity(page: PDFPageInput) -> str:
        return asyncio.run(registry.resolve(page)).name

    assert len(identity(base)) <= 256
    assert identity(base) == identity(base.model_copy(update={"text": "changed"}))
    variants = [
        base.model_copy(update=change)
        for change in ({"document_id": base.document_id + ":"}, {"document_sha256": "b" * 64}, {"page_number": 2})
    ]
    assert len({identity(base), *(identity(page) for page in variants)}) == 4
    source = asyncio.run(registry.resolve(base))
    with pytest.raises(ValueError):
        PDFPageSource.model_validate(source.model_dump() | {"page_number": 2})


@pytest.mark.parametrize("changes", [{"page_number": 0}, {"page_number": True}, {"document_sha256": "A" * 64}])
def test_manually_supplied_pages_validate_location(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PDFPageInput.model_validate(
            {"document_id": "report", "document_sha256": "a" * 64, "page_number": 1, "text": ""} | changes
        )
