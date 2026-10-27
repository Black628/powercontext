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
import gzip
import json
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    GetMemoryEntryRequest,
    MemoryCitation,
    PrepareContextRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.context_pack import (
    ContextPack,
    ContextPackDelta,
    apply_context_pack_delta,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource

_FIXTURE = Path(__file__).parents[1] / "fixtures/context_pack/meeting_turns.json"


class _ConversationPipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="conversation-turn", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def test_incremental_context_preserves_empty_results_citations_and_authorization(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'context.db'}"))
        async with open_builtin_runtime(config, candidate_pipeline=_ConversationPipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Context", summary="Incremental context", idempotency_key="incremental-context")
            )
            context = runtime.context.for_scope(scope.scope_id)
            memory = runtime.memory.for_scope(scope.scope_id)
            sources = runtime.sources.for_scope(scope.scope_id)
            request = PrepareContextRequest(query="zircon")
            initial = await context.prepare_incremental(request)
            assert isinstance(initial, ContextPack)
            assert initial.items == ()
            await sources.capture(
                CaptureSource(source_id="turn-1", content="zircon evidence with an exact citation", metadata={})
            )
            await memory.flush()
            result = await context.prepare_incremental(request, base_pack=ContextPack.from_bytes(initial.to_bytes()))
            assert isinstance(result, ContextPackDelta)
            ready = apply_context_pack_delta(initial, ContextPackDelta.from_bytes(result.to_bytes()))
            ordinary = await context.prepare(request)
            assert b"".join(ready.items).decode() == ordinary.content
            assert ordinary.content is not None
            payload = json.loads(
                ordinary.content.split("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n", 1)[1].split(
                    "\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1", 1
                )[0]
            )
            citation = MemoryCitation.model_validate(payload["items"][0]["citation"])
            await sources.capture(CaptureSource(source_id="turn-2", content="zircon later evidence", metadata={}))
            await memory.flush()
            historical = await memory.get(GetMemoryEntryRequest(citation=citation))
            assert historical.entry.text == "zircon evidence with an exact citation"
            empty_request = PrepareContextRequest(query="unmatchablexylophone")
            assert (await context.prepare(empty_request)).content is None
            empty_delta = await context.prepare_incremental(empty_request, base_pack=ready)
            assert isinstance(empty_delta, ContextPackDelta)
            assert apply_context_pack_delta(ready, empty_delta).items == ()

            async def deny(_scopes: tuple[str, ...]) -> None:
                raise PermissionError("scope access denied")  # noqa: TRY003

            with pytest.raises(PermissionError, match="scope access denied"):
                await context.prepare_incremental(request, authorize_scopes=deny)

    asyncio.run(scenario())


def test_context_pack_transfer_budget_for_forty_real_meeting_rounds(tmp_path: Path) -> None:
    """Measure actual native prepare output, including every transmitted protocol byte."""

    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    turns = fixture["turns"]

    async def scenario() -> dict[str, object]:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'meeting.db'}"))
        async with open_builtin_runtime(config, candidate_pipeline=_ConversationPipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Meeting", summary="QMSum context transmission", idempotency_key="meeting-transmission"
                )
            )
            sources = runtime.sources.for_scope(scope.scope_id)
            memory = runtime.memory.for_scope(scope.scope_id)
            context = runtime.context.for_scope(scope.scope_id)

            async def capture(index: int) -> None:
                turn = turns[index]
                await sources.capture(
                    CaptureSource(
                        source_id=f"meeting:{fixture['meeting_id']}:{turn['turn_index']}",
                        content=f"{turn['speaker']}: {turn['text']}",
                        metadata={},
                    )
                )

            for index in range(8):
                await capture(index)
            while (await memory.flush()).processed:
                pass
            raw_bytes = incremental_bytes = full_pack_bytes = gzip_bytes = 0
            unique: set[bytes] = set()
            receiver: ContextPack | None = None
            rounds: list[dict[str, object]] = []
            queries = ("remote", "price", "market", "control")
            additions = {8: 8, 16: 9, 24: 10, 32: 11}
            for index in range(40):
                if index in additions:
                    await capture(additions[index])
                    while (await memory.flush()).processed:
                        pass
                query = queries[index // 10]
                request = PrepareContextRequest(query=query, max_bytes=8192)
                ordinary = await context.prepare(request)
                assert ordinary.content
                raw = ordinary.content.encode()
                outgoing = await context.prepare_incremental(request, base_pack=receiver)
                wire = outgoing.to_bytes()
                if receiver is None:
                    assert isinstance(outgoing, ContextPack)
                    receiver = ContextPack.from_bytes(wire)
                else:
                    assert isinstance(outgoing, ContextPackDelta)
                    receiver = apply_context_pack_delta(
                        ContextPack.from_bytes(receiver.to_bytes()), ContextPackDelta.from_bytes(wire)
                    )
                assert b"".join(receiver.items) == raw
                assert receiver == ContextPack.from_content(ordinary.content)
                raw_bytes += len(raw)
                incremental_bytes += len(wire)
                full_pack_bytes += len(receiver.to_bytes())
                gzip_count = len(gzip.compress(raw, compresslevel=9, mtime=0))
                gzip_bytes += gzip_count
                unique.add(raw)
                rounds.append({
                    "round": index + 1,
                    "query": query,
                    "raw_bytes": len(raw),
                    "incremental_bytes": len(wire),
                    "full_pack_bytes": receiver.byte_count,
                    "gzip_bytes": gzip_count,
                })
        return {
            "meeting_id": fixture["meeting_id"],
            "source_url": fixture["source_url"],
            "source_sha256": fixture["source_sha256"],
            "initial_turns": 8,
            "distinct_contexts": len(unique),
            "round_count": len(rounds),
            "raw_full_bytes": raw_bytes,
            "incremental_bytes": incremental_bytes,
            "full_pack_bytes": full_pack_bytes,
            "gzip_bytes": gzip_bytes,
            "savings_vs_raw": 1 - incremental_bytes / raw_bytes,
            "savings_vs_full_pack": 1 - incremental_bytes / full_pack_bytes,
            "savings_vs_gzip": 1 - incremental_bytes / gzip_bytes,
            "rounds": rounds,
        }

    report = asyncio.run(scenario())
    print("CONTEXT_PACK_MEASUREMENT " + json.dumps(report, sort_keys=True, separators=(",", ":")))
    assert report["round_count"] == 40
    distinct = report["distinct_contexts"]
    savings = report["savings_vs_raw"]
    assert isinstance(distinct, int) and distinct >= 4
    assert isinstance(savings, float) and savings >= 0.45
