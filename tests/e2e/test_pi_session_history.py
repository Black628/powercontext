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
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import JsonValue

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
from powercontext.sources import (
    ConnectorBinding,
    ConnectorRunResult,
    ConnectorRunStatus,
    SourceObservation,
    TextEvidence,
)
from powercontext.sources.pi_session import PiEventInput
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY

_RUN_CONNECTOR = """
import asyncio
import json
import sys
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig, CommitConnectorCheckpoint, SubmitSourceObservation, open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.errors import ConnectorSubmissionRejectedError
from powercontext.sources import (
    ConnectorBinding, SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport,
)
from powercontext.sources.connectors import ConnectorLifecycle
from powercontext.sources.pi_session import PI_EVENT_SOURCE_DEFINITION, PiSessionConnector

class Bridge:
    def __init__(self, runtime, registry):
        self.runtime, self.registry = runtime, registry

    async def submit(self, binding, item_id, definition_name, value):
        if value.event_id == "tail" and sys.argv[4] == "fail":
            raise OSError("intentional test sink failure")
        if value.event_id == "tail" and sys.argv[4] == "reject":
            raise ConnectorSubmissionRejectedError("intentional test rejection")
        source = await self.registry.resolve(value)
        return (await self.runtime.ingestion.submit(SubmitSourceObservation(
            scope_id=binding.scope_id, observation=project_source_for_transport(self.registry, source)
        ))).source_ref

    async def load(self, binding):
        return (await self.runtime.ingestion.checkpoint(binding)).checkpoint

    async def save(self, binding, checkpoint, *, expected):
        await self.runtime.ingestion.commit(CommitConnectorCheckpoint(
            binding=binding, checkpoint=checkpoint, expected=expected
        ))

async def run():
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{sys.argv[1]}"))
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    async with open_builtin_runtime(config) as runtime:
        scope = await runtime.scopes.create(ScopeDraft(title="Pi", summary="Raw events", idempotency_key="pi-history"))
        await runtime.ingestion.register(manifest_for_definition(PI_EVENT_SOURCE_DEFINITION))
        binding = ConnectorBinding(scope_id=scope.scope_id, binding_id="history", connector_name="pi-session-history", connector_version="1")
        bridge = Bridge(runtime, registry)
        result = await ConnectorLifecycle(sink=bridge, checkpoints=bridge).run(
            PiSessionConnector(sys.argv[2], sys.argv[3] or None), binding
        )
        print(json.dumps(result.model_dump(mode="json")))

try:
    asyncio.run(run())
except ValueError:
    print("PI_VALUE_ERROR", file=sys.stderr)
    raise
"""


def _process(database: Path, path: Path, leaf: str, mode: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _RUN_CONNECTOR, str(database), str(path), leaf, mode],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _run(database: Path, path: Path, leaf: str, mode: str = "") -> ConnectorRunResult:
    result = _process(database, path, leaf, mode)
    assert result.returncode == 0, result.stderr
    return ConnectorRunResult.model_validate_json(result.stdout)


def _write(path: Path, records: list[dict[str, JsonValue]]) -> None:
    header = {"type": "session", "version": 3, "id": "persisted-session"}
    path.write_bytes("\n".join(json.dumps(row, ensure_ascii=False) for row in [header, *records]).encode())


def _message(event_id: str, parent: str | None, content: str) -> dict[str, JsonValue]:
    return {"type": "message", "id": event_id, "parentId": parent, "message": {"role": "user", "content": content}}


async def _stored(database: Path, binding: ConnectorBinding) -> tuple[JsonValue | None, dict[str, SourceObservation]]:
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
    async with open_builtin_runtime(config) as runtime:
        checkpoint = (await runtime.ingestion.checkpoint(binding)).checkpoint
    async with open_builtin_contexts(config) as contexts:
        journal = await (await contexts.get(binding.scope_id)).sources.journal.entries()
        stored: dict[str, SourceObservation] = {}
        for entry in journal:
            assert isinstance(entry.source, SourceObservation)
            stored[entry.source_ref.source_id] = entry.source
    return checkpoint, stored


class _EventEvidencePipeline:
    async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
        entries = []
        for source in request.sources:
            text = source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"]
            for index in range(0, len(text), 1500):
                entries.append(
                    MemoryEntryInput(kind=f"pi-evidence-{index}", text=text[index : index + 1500], sources=(source,))
                )
        return tuple(entries)


def test_branch_checkpoint_and_complete_tool_evidence_survive_separate_processes(tmp_path: Path) -> None:
    path, database = tmp_path / "pi.jsonl", tmp_path / "pi.db"
    tool_text = "zircon 工具结果\n" * 1200 + "COMPLETE-END"
    records: list[dict[str, JsonValue]] = [
        _message("root", None, "shared zircon request"),
        _message("left", "root", "left zircon evidence"),
        {"type": "model_change", "id": "control", "parentId": "root"},
        _message("right", "control", "right zircon evidence"),
        {
            "type": "message",
            "id": "tool",
            "parentId": "right",
            "message": {"role": "toolResult", "toolName": "read", "toolCallId": "call", "content": tool_text},
        },
    ]
    _write(path, records)
    first = _run(database, path, "left")
    second = _run(database, path, "tool")
    assert len(first.items) == 2 and len(second.items) == 2
    assert second.previous_checkpoint == first.committed_checkpoint
    replay = _run(database, path, "tool")
    assert replay.items == () and replay.committed_checkpoint == second.committed_checkpoint
    checkpoint, stored = asyncio.run(_stored(database, first.binding))
    assert checkpoint == second.committed_checkpoint
    assert len(stored) == 4
    events = {PiEventInput.model_validate(source.payload).event_id: source for source in stored.values()}
    assert set(events) == {"root", "left", "right", "tool"}
    tool_event = PiEventInput.model_validate(events["tool"].payload)
    assert tool_event.record == records[-1]
    assert tool_event.content == f"toolResult: [tool_result read call] {tool_text}"

    async def cite_all_events() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
        async with open_builtin_runtime(config, candidate_pipeline=_EventEvidencePipeline()) as runtime:
            memory = runtime.memory.for_scope(first.binding.scope_id)
            for _ in range(5):
                if not (await memory.flush()).processed:
                    break
            search = await memory.search(SearchMemoryRequest(query="zircon", mode="fts", limit=100))
            cited = set()
            for hit in search.hits:
                citation = MemoryCitation(
                    memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
                )
                entry = await memory.get(GetMemoryEntryRequest(citation=citation))
                for ref in entry.entry.sources:
                    full = TextEvidence.model_validate(
                        stored[ref.source_id].projection(TEXT_EVIDENCE_PROJECTION_KEY)
                    ).content
                    assert entry.entry.text in full
                    cited.add(ref.source_id)
            assert cited == set(stored)

    asyncio.run(cite_all_events())
    # A metadata-only edit on an unselected imported branch must also fail.
    records[1]["annotation"] = "rewritten"
    _write(path, records)
    rejected = _process(database, path, "tool")
    assert rejected.returncode != 0 and "PI_VALUE_ERROR" in rejected.stderr
    assert asyncio.run(_stored(database, first.binding)) == (checkpoint, stored)
    del records[1]["annotation"]
    records.append(_message("append", "tool", "later evidence"))
    _write(path, records)
    path.write_bytes(path.read_bytes() + b"\n{invalid JSON}")
    malformed = _process(database, path, "append")
    assert malformed.returncode != 0 and "PI_VALUE_ERROR" in malformed.stderr
    assert asyncio.run(_stored(database, first.binding)) == (checkpoint, stored)
    _write(path, records)
    repaired = _run(database, path, "append")
    assert repaired.previous_checkpoint == checkpoint and len(repaired.items) == 1
    assert len(asyncio.run(_stored(database, first.binding))[1]) == 5


@pytest.mark.parametrize("mode", ["fail", "reject"])
def test_partial_sink_failure_keeps_checkpoint_and_retry_deduplicates_sources(tmp_path: Path, mode: str) -> None:
    path, database = tmp_path / "pi.jsonl", tmp_path / "pi.db"
    records = [_message("root", None, "baseline")]
    _write(path, records)
    baseline = _run(database, path, "root")
    records.extend([_message("new", "root", "accepted before failure"), _message("tail", "new", "retry me")])
    _write(path, records)
    failed = _process(database, path, "tail", mode)
    if mode == "fail":
        assert failed.returncode != 0 and "OSError" in failed.stderr
    else:
        result = ConnectorRunResult.model_validate_json(failed.stdout)
        assert result.status is ConnectorRunStatus.INCOMPLETE
    checkpoint, stored = asyncio.run(_stored(database, baseline.binding))
    assert checkpoint == baseline.committed_checkpoint and len(stored) == 2
    # The accepted event is immutable even though its run did not commit progress.
    records[1] = _message("new", "root", "changed before retry")
    _write(path, records)
    conflict = _process(database, path, "tail")
    assert conflict.returncode != 0 and "SourceConflictError" in conflict.stderr
    assert asyncio.run(_stored(database, baseline.binding)) == (checkpoint, stored)
    records[1] = _message("new", "root", "accepted before failure")
    _write(path, records)
    recovered = _run(database, path, "tail")
    assert recovered.previous_checkpoint == checkpoint and len(recovered.items) == 2
    assert len(asyncio.run(_stored(database, baseline.binding))[1]) == 3
    assert _run(database, path, "tail").items == ()
