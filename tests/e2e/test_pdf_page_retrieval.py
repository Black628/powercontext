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
import io
import subprocess
import sys
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    RememberMemoryRequest,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceObservation
from powercontext.sources.pdf_pages import PDFPageInput, search_pdf_pages
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY

_CAPTURE = """
import asyncio
import sys
from pathlib import Path
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, SubmitSourceObservation, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.pdf_pages import PDF_PAGE_SOURCE_DEFINITION, PDFDocumentInput, parse_pdf_document

async def capture():
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{sys.argv[1]}"))
    async with open_builtin_runtime(config) as runtime:
        scope = await runtime.scopes.create(ScopeDraft(title="Reports", summary="PDFs", idempotency_key="pdfs"))
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        for name, file in zip(("ACME_2025_10K", "ACME_2025_10K", "OTHER_2025_10K"), sys.argv[2:], strict=True):
            for page in parse_pdf_document(PDFDocumentInput(document_id=name, content=Path(file).read_bytes())):
                observation = project_source_for_transport(registry, await registry.resolve(page))
                request = SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                await runtime.ingestion.submit(request)
                await runtime.ingestion.submit(request)
        print(scope.scope_id)

asyncio.run(capture())
"""


def _pdf(pages: list[str], revision: str) -> bytes:
    writer = PdfWriter()
    writer.add_metadata({"/Title": revision})
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        if text:
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
            })
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = stream
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class _PagePipeline:
    async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind=kind, text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"], sources=(source,)
            )
            for source in request.sources
            for kind in ("pdf-evidence", "duplicate-page")
        )


def test_pdf_evidence_survives_capture_process_and_runtime_restart(tmp_path: Path) -> None:
    texts = [
        "Consolidated statement index",
        "CONSOLIDATED STATEMENTS OF CASH FLOWS\n2025 Depreciation and amortization 42 Capital expenditure 91",
        "CONSOLIDATED STATEMENTS OF INCOME\n2025 Net revenue 810 Operating income 120",
        "Contingencies: the company is defending litigation over a patent",
        "",
        "Discussion of EBITDA, operating income, depreciation, amortization, cash flows and income statement. " * 20,
    ]
    files = [tmp_path / name for name in ("original.pdf", "revised.pdf", "other.pdf")]
    files[0].write_bytes(_pdf(texts, "original"))
    files[1].write_bytes(_pdf(texts, "revision"))
    files[2].write_bytes(_pdf([texts[1] + " EBITDA capex income margin " * 3], "competitor"))
    database = tmp_path / "reports.db"
    captured = subprocess.run(
        [sys.executable, "-c", _CAPTURE, str(database), *(str(file) for file in files)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    scope_id = captured.stdout.strip()
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))

    async def retrieve() -> None:
        async with open_builtin_contexts(config) as contexts:
            journal = await (await contexts.get(scope_id)).sources.journal.entries()
            assert len(journal) == 13
            observations: dict[str, SourceObservation] = {}
            for item in journal:
                assert isinstance(item.source, SourceObservation)
                observations[item.source_ref.source_id] = item.source

        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            memory = runtime.memory.for_scope(scope_id)
            for _ in range(14):
                if not (await memory.flush()).processed:
                    break
            else:
                raise AssertionError("PDF ingestion did not drain")  # noqa: TRY003
            await memory.remember(
                RememberMemoryRequest(
                    entries=(
                        MemoryEntryInput(
                            kind="unrelated", text="ACME 2025 EBITDA income depreciation cash flows " * 20
                        ),
                    )
                )
            )

        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope_id)
            citations = await search_pdf_pages(
                memory, "What is ACME's FY2025 EBITDA using operating income and D&A from cash flows?", limit=4
            )
            assert len(citations) == 4
            locations = []
            for citation in citations:
                entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                (reference,) = entry.sources
                observation = observations[reference.source_id]
                assert isinstance(observation, SourceObservation)
                page = PDFPageInput.model_validate(observation.payload)
                assert entry.text == f"{page.document_id}\n{page.text}".strip()
                locations.append((page.document_id, page.document_sha256, page.page_number))
            assert len(set(locations)) == 4
            assert {page_number for doc, _, page_number in locations if doc == "ACME_2025_10K"} == {2, 3}
            legal = await search_pdf_pages(memory, "Does ACME have ongoing legal battles?", limit=1)
            assert len(legal) == 1
            entry = (await memory.get(GetMemoryEntryRequest(citation=legal[0]))).entry
            assert "patent" in entry.text
            all_citations = await search_pdf_pages(memory, "cash flow income litigation", limit=100)
            references = [
                (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.sources[0]
                for citation in all_citations
            ]
            assert len({reference.source_id for reference in references}) == len(references)
            assert await search_pdf_pages(memory, "cash", limit=0) == ()
            assert await search_pdf_pages(memory, "cash", limit=-1) == ()
            assert await search_pdf_pages(memory, " \t") == ()
            assert runtime.scopes is not None
            other_scope = await runtime.scopes.create(
                ScopeDraft(title="Empty", summary="Isolated", idempotency_key="empty")
            )
            assert await search_pdf_pages(runtime.memory.for_scope(other_scope.scope_id), "ACME cash") == ()

        async with open_builtin_runtime(config) as runtime:
            restored = await runtime.memory.for_scope(scope_id).get(GetMemoryEntryRequest(citation=citations[0]))
            assert restored.citation == citations[0]

    asyncio.run(retrieve())
