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
import subprocess
import sys
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SearchMemoryRequest,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.sources import SourceDefinitionRegistry, SourceObservation, TextEvidence
from powercontext.sources.csv_rows import CSV_ROW_SOURCE_DEFINITION, CSVRowInput, CSVRowSource, csv_row_source_id
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY

_CAPTURE_IN_ANOTHER_PROCESS = """
import asyncio
import sys
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, SubmitSourceObservation, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.csv_rows import CSV_ROW_SOURCE_DEFINITION, CSVDocumentInput, parse_csv_document

async def capture():
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{sys.argv[1]}"))
    rows = parse_csv_document(CSVDocumentInput(
        document_id="records:2026", primary_key="id",
        content=b"id,note,amount,empty\\n 001 ,zircon north,0001,\\n002,cobalt south,2.00,\\n"
    ))
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    async with open_builtin_runtime(config) as runtime:
        scope = await runtime.scopes.create(ScopeDraft(title="CSV", summary="Row evidence", idempotency_key="csv"))
        await runtime.ingestion.register(manifest_for_definition(CSV_ROW_SOURCE_DEFINITION))
        for row in rows:
            observation = project_source_for_transport(registry, await registry.resolve(row))
            request = SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
            await runtime.ingestion.submit(request)
            await runtime.ingestion.submit(request)
        print(scope.scope_id)

asyncio.run(capture())
"""


class _RowEvidencePipeline:
    async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="csv-row",
                text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                sources=(source,),
            )
            for source in request.sources
        )


def test_csv_rows_remain_readable_and_citable_after_capture_process_exits(tmp_path: Path) -> None:
    database = tmp_path / "csv.db"
    captured = subprocess.run(
        [sys.executable, "-c", _CAPTURE_IN_ANOTHER_PROCESS, str(database)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    scope_id = captured.stdout.strip()
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))

    async def read_and_search() -> None:
        registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
        expected = {
            " 001 ": (" 001 ", "zircon north", "0001", ""),
            "002": ("002", "cobalt south", "2.00", ""),
        }
        async with open_builtin_contexts(config) as contexts:
            journal = await (await contexts.get(scope_id)).sources.journal.entries()
            assert len(journal) == 2
            for entry in journal:
                source = entry.source
                assert isinstance(source, SourceObservation)
                restored = CSVRowSource.model_validate(source.payload)
                row = await registry.read(restored)
                assert isinstance(row, CSVRowInput)
                assert row.columns == ("id", "note", "amount", "empty")
                assert row.values == expected[row.primary_key_value]
                assert source.projection(TEXT_EVIDENCE_PROJECTION_KEY) == registry.project(
                    restored, TEXT_EVIDENCE_PROJECTION_KEY
                )

        async with open_builtin_runtime(config, candidate_pipeline=_RowEvidencePipeline()) as runtime:
            memory = runtime.memory.for_scope(scope_id)
            processed = 0
            for _ in range(3):
                result = await memory.flush()
                if not result.processed:
                    break
                processed += result.source_count
            assert processed == 2
            search = await memory.search(SearchMemoryRequest(query="zircon", mode="fts", limit=10))
            assert len(search.hits) == 1
            hit = search.hits[0]
            citation = MemoryCitation(
                memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
            )

        async with open_builtin_runtime(config) as runtime:
            entry = await runtime.memory.for_scope(scope_id).get(GetMemoryEntryRequest(citation=citation))
            assert entry.entry.text == '{"id":" 001 ","note":"zircon north","amount":"0001","empty":""}'
            assert len(entry.entry.sources) == 1
            reference = entry.entry.sources[0]
            assert reference.source_type == "csv-row"
            assert reference.source_id == csv_row_source_id("records:2026", " 001 ")

        async with open_builtin_contexts(config) as contexts:
            journal = await (await contexts.get(scope_id)).sources.journal.entries()
            source = next(item.source for item in journal if item.source_ref.source_id == reference.source_id)
            assert isinstance(source, SourceObservation)
            evidence = TextEvidence.model_validate(source.projection(TEXT_EVIDENCE_PROJECTION_KEY))
            assert evidence.content == entry.entry.text
            assert evidence.metadata["columns"] == ["id", "note", "amount", "empty"]
            assert evidence.metadata["row"] == dict(
                zip(("id", "note", "amount", "empty"), expected[" 001 "], strict=True)
            )

    asyncio.run(read_and_search())
