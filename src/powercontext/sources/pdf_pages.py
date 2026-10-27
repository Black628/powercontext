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

"""One ``CAPTURED`` Source per physical PDF page, plus page-level retrieval."""

from __future__ import annotations

import hashlib
import io
import math
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from pydantic import BaseModel, field_validator, model_validator
from pypdf import PdfReader

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import MemoryCitation

SOURCE_TYPE = "pdf-page"
DEFINITION_VERSION = "1"
_WORD = re.compile(r"[a-z]+|\d+")
_STOPWORDS = frozenset(
    "a an the of and or in on for to is are was were be been being what which who whom how why "
    "when where does do did has have had it its this that these those as at by from with about "
    "into than then so if not no we our you your they their there here can could should would "
    "may might must will shall per each also any all such other more most less least".split()
)
_BM25_K1 = 1.2
_BM25_B = 0.75
_PHRASE_BOOST = 2.0
# Finance questions name metrics the statements themselves spell out differently.
_PHRASES: dict[str, tuple[str, ...]] = {
    "ebitda": ("statement of income", "statement of cash flows", "depreciation and amortization", "operating income"),
    "margin": ("statement of income", "gross margin", "operating margin"),
    "capex": ("capital expenditures", "capital spending"),
    "legal": ("legal proceedings", "litigation", "regulatory"),
    "battle": ("legal proceedings", "litigation", "claims"),
    "geograph": ("north america", "latin america", "europe", "asia pacific", "middle east"),
    "income": ("consolidated statement of income", "statement of income", "operating income"),
    "cash": ("consolidated statement of cash flows", "statement of cash flows", "cash flows"),
    "statement": (
        "consolidated statement of income",
        "consolidated statement of cash flows",
        "statement of income",
        "statement of cash flows",
    ),
    "restructur": ("restructuring", "severance"),
}


class PDFDocumentInput(BaseModel):
    """Raw bytes of one complete PDF document."""

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        _validate_document_id(value)
        return value


class PDFPageInput(BaseModel):
    """One physical page of a PDF document, in reading order."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """A captured page whose identity binds the document revision and page number."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str

    @model_validator(mode="after")
    def validate_identity(self) -> PDFPageSource:
        if self.name != self.source_id:
            raise ValueError("PDF page Source name must equal its source_id")  # noqa: TRY003
        return self


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Derive the stable identity of one page of one document revision."""

    _validate_document_id(document_id)
    if not isinstance(document_sha256, str) or len(document_sha256) != 64:
        raise ValueError("document_sha256 must be a lowercase 64-character hex digest")  # noqa: TRY003
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise ValueError("page_number must be a positive integer")  # noqa: TRY003
    identity = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        digest = hashlib.sha256(f"{document_id}\x00{document_sha256}\x00{page_number}".encode()).hexdigest()
        identity = f"pdf:sha256:{digest}"
    return identity


def parse_pdf_document(value: PDFDocumentInput, /) -> tuple[PDFPageInput, ...]:
    """Extract every physical page before returning any of them."""

    raw = value.content
    if not raw:
        raise ValueError("PDF document must not be empty")  # noqa: TRY003
    document_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a user password")  # noqa: TRY003
        pages = tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=document_sha256,
                page_number=number,
                text=(page.extract_text() or "").strip(),
            )
            for number, page in enumerate(reader.pages, start=1)
        )
    except ValueError:
        raise
    except Exception as error:  # noqa: BLE001 - any reader failure must surface as ValueError
        raise ValueError(f"PDF document could not be parsed: {error}") from error
    if not pages:
        raise ValueError("PDF document contains no pages")  # noqa: TRY003
    return pages


class _PDFPageAdapter:
    """Resolve parsed pages into Sources and read them back without loss."""

    name = SOURCE_TYPE
    input_class = PDFPageInput
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            definition_version=DEFINITION_VERSION,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class _PDFPageTextEvidenceProjection:
    """Standard ``powercontext.text-evidence`` view of one page."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=SOURCE_TYPE,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION: AdapterSourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = (
    AdapterSourceDefinition(
        adapter=_PDFPageAdapter(),
        version=DEFINITION_VERSION,
        projections=(_PDFPageTextEvidenceProjection(),),
    )
)


async def search_pdf_pages(
    memory: Any,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Return exact citations for at most ``limit`` distinct evidence pages."""

    from powercontext.builtin.artifacts.memory.models import MemoryCitation
    from powercontext.builtin.runtime.models import SearchMemoryRequest

    if limit <= 0 or not isinstance(question, str) or not question.strip():
        return ()

    records = await _scoped_entries(memory)
    if records:
        ranked = _rank_pages([record.entry.text for record in records], question)
        return tuple(records[index].citation for index in ranked[:limit])

    return await _lexical_fallback(memory, question, limit, SearchMemoryRequest, MemoryCitation)


async def _scoped_entries(memory: Any) -> list[Any]:
    """Read the scoped Memory head so every page can be scored locally."""

    try:
        page = await memory.list()
    except Exception:  # noqa: BLE001 - enumeration is an optimization, not a requirement
        return []
    entries = getattr(page, "entries", ()) or ()
    return list(entries)


def _rank_pages(texts: list[str], question: str) -> list[int]:
    """Rank pages by BM25 over the question plus finance-specific phrase evidence."""

    n = len(texts)
    if not n:
        return []
    counts = [_token_counts(text) for text in texts]
    lengths = [sum(counter.values()) for counter in counts]
    average = sum(lengths) / n or 1.0
    document_frequency: dict[str, int] = {}
    for counter in counts:
        for term in counter:
            document_frequency[term] = document_frequency.get(term, 0) + 1
    inverse_frequency = {
        term: math.log((n - frequency + 0.5) / (frequency + 0.5) + 1.0)
        for term, frequency in document_frequency.items()
    }

    terms = sorted({term for term in _WORD.findall(question.lower()) if term not in _STOPWORDS and len(term) > 1})
    scores = [0.0] * n
    for term in terms:
        weight = inverse_frequency.get(term)
        if weight is None:
            continue
        for index, counter in enumerate(counts):
            frequency = counter.get(term)
            if not frequency:
                continue
            normalizer = _BM25_K1 * (1.0 - _BM25_B + _BM25_B * lengths[index] / average)
            scores[index] += weight * (frequency * (_BM25_K1 + 1.0)) / (frequency + normalizer)

    phrases = _phrase_weights(terms, inverse_frequency)
    if phrases:
        flattened = [" ".join(_WORD.findall(text.lower())) for text in texts]
        for phrase, weight in phrases:
            for index, text in enumerate(flattened):
                if phrase in text:
                    scores[index] += weight

    return sorted(range(n), key=lambda index: (scores[index], -index), reverse=True)


def _token_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for term in _WORD.findall(text.lower()):
        if term in _STOPWORDS or len(term) <= 1:
            continue
        counts[term] = counts.get(term, 0) + 1
    return counts


def _phrase_weights(
    terms: list[str],
    inverse_frequency: dict[str, float],
) -> list[tuple[str, float]]:
    """Expand finance metrics into the wording 10-K statements actually use."""

    weights: dict[str, float] = {}
    for term in terms:
        for key, phrases in _PHRASES.items():
            if not term.startswith(key):
                continue
            for phrase in phrases:
                normalized = " ".join(_WORD.findall(phrase))
                if not normalized or normalized in weights:
                    continue
                weights[normalized] = _PHRASE_BOOST * max(
                    (inverse_frequency.get(token, 0.0) for token in normalized.split()),
                    default=0.0,
                )
    return [(phrase, weight) for phrase, weight in weights.items() if weight > 0.0]


async def _lexical_fallback(
    memory: Any,
    question: str,
    limit: int,
    request_class: Any,
    citation_class: Any,
) -> tuple[Any, ...]:
    """Use the engine's lexical channel when the scope cannot be enumerated."""

    page = await memory.search(request_class(query=question, mode="fts", limit=max(limit * 20, 120)))
    citations: list[Any] = []
    seen: set[str] = set()
    for hit in page.hits:
        if hit.entry_id in seen:
            continue
        seen.add(hit.entry_id)
        citations.append(
            citation_class(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
        )
        if len(citations) >= limit:
            break
    return tuple(citations)



def _validate_document_id(value: str) -> None:
    if not isinstance(value, str) or not value or not value.strip():
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
