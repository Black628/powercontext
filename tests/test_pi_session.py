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

"""Regression coverage for Pi archive integrity and lifecycle failure recovery."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from powercontext.errors import ConnectorSubmissionRejectedError
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    ConnectorBinding,
    ConnectorRunStatus,
    SourceDefinitionRegistry,
    SourceMaterialization,
    SourceRef,
)
from powercontext.sources.connectors import ConnectorLifecycle
from powercontext.sources.pi_session import (
    PI_EVENT_SOURCE_DEFINITION,
    PiSessionConnector,
    PiSessionInput,
    parse_pi_session,
)


def archive(records, *, session_id="tree"):
    header = {"type": "session", "version": 3, "id": session_id}
    return "\n".join(json.dumps(record, ensure_ascii=False) for record in [header, *records]).encode()


def message(event_id, parent_id, text):
    return {"type": "message", "id": event_id, "parentId": parent_id, "message": {"role": "user", "content": text}}


@pytest.mark.parametrize(
    "raw",
    [
        b'{"type":"session","version":3.0,"id":"x"}',
        b'{"type":"session","version":3,"id":" "}',
        archive([{"id": "a", "type": "message", "message": {"role": "user", "content": "missing parent"}}]),
        archive([message("a", None, "ok"), message("b", "later", "bad"), message("later", "a", "later")]),
        archive([message("a", None, "ok")]) + b'\n{"id":"unselected","parentId":null,"type":"x","n":NaN}',
    ],
)
def test_malformed_off_path_records_are_not_ignored(raw):
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=raw, leaf_id="a"))


def test_tool_call_json_and_visible_text_preserve_order_and_unicode():
    records = [
        {
            "type": "message",
            "id": "a",
            "parentId": None,
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "  before  "},
                    {"type": "thinking", "thinking": "not text evidence"},
                    {"type": "toolCall", "name": "read", "id": "c", "arguments": {"z": "中文", "a": {"z": 2, "b": 1}}},
                    {"type": "image", "data": "not text evidence"},
                    {"type": "text", "text": "after\n"},
                ],
            },
        }
    ]
    (event,) = parse_pi_session(PiSessionInput(content=archive(records)))
    assert event.content == 'assistant:   before  \n[tool_call read c] {"a":{"b":1,"z":2},"z":"中文"}\nafter\n'
    assert event.record == records[0]


def test_empty_and_deep_control_paths():
    header = archive([])
    assert parse_pi_session(PiSessionInput(content=header)) == ()
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=header, leaf_id="absent"))
    records = [message("root", None, "root")]
    for i in range(2000):
        records.append({"type": "model_change", "id": str(i), "parentId": records[-1]["id"]})
    records.append(message("tail", "1999", "tail"))
    assert [event.event_id for event in parse_pi_session(PiSessionInput(content=archive(records)))] == ["root", "tail"]


def test_unicode_line_separators_inside_json_strings_are_preserved():
    text = "第一段\u2028第二段\u2029第三段"
    (event,) = parse_pi_session(PiSessionInput(content=archive([message("a", None, text)])))
    assert event.content == "user: " + text


async def test_identity_is_unambiguous_and_captured_values_are_detached():
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    (first,) = parse_pi_session(PiSessionInput(content=archive([message("b\0c", None, "same")], session_id="a")))
    (second,) = parse_pi_session(PiSessionInput(content=archive([message("c", None, "same")], session_id="a\0b")))
    source = await registry.resolve(first)
    assert source.materialization is SourceMaterialization.CAPTURED
    assert source.name != (await registry.resolve(second)).name
    assert len(source.name) <= 256
    first.record["extra"] = "mutated after resolve"
    captured = await registry.read(source)
    assert "extra" not in captured.record
    captured.record["extra"] = "mutated after read"
    assert "extra" not in (await registry.read(source)).record


async def test_large_tool_result_is_not_truncated_in_source_or_projection():
    text = "完整工具结果\n" * 20_000
    record = {
        "type": "message",
        "id": "tool",
        "parentId": None,
        "message": {"role": "toolResult", "toolName": "read", "toolCallId": "call", "content": text},
        "provider_metadata": {"preserve": [1, 2, 3]},
    }
    (event,) = parse_pi_session(PiSessionInput(content=archive([record])))
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    source = await registry.resolve(event)
    assert (await registry.read(source)).record == record
    evidence = registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY)
    assert evidence["content"] == "toolResult: [tool_result read call] " + text


class Checkpoints:
    def __init__(self):
        self.value = None

    async def load(self, binding):
        return deepcopy(self.value)

    async def save(self, binding, checkpoint, *, expected):
        assert expected == self.value
        self.value = deepcopy(checkpoint)


class Sink:
    def __init__(self):
        self.records = {}
        self.fail_event = None
        self.reject_event = None

    async def submit(self, binding, item_id, definition_name, value):
        if value.event_id == self.fail_event:
            raise OSError("simulated storage failure")  # noqa: TRY003
        if value.event_id == self.reject_event:
            raise ConnectorSubmissionRejectedError("simulated rejection")  # noqa: TRY003
        # Successful retries retain the same event value and identity.
        if item_id in self.records:
            assert self.records[item_id] == value
        self.records[item_id] = value.model_copy(deep=True)
        return SourceRef(source_type=definition_name, source_id=item_id)


def binding():
    return ConnectorBinding(
        scope_id="scope", binding_id="pi", connector_name="pi-session-history", connector_version="1"
    )


async def test_failure_rejection_and_reconstructed_checkpoint_resume(tmp_path):
    path = tmp_path / "session.jsonl"
    records = [message("root", None, "root")]
    path.write_bytes(archive(records))
    sink, checkpoints = Sink(), Checkpoints()
    lifecycle = ConnectorLifecycle(sink=sink, checkpoints=checkpoints)
    first = await lifecycle.run(PiSessionConnector(path), binding())
    original = deepcopy(checkpoints.value)
    records.extend([message("next", "root", "next"), message("last", "next", "last")])
    path.write_bytes(archive(records))
    sink.fail_event = "last"
    with pytest.raises(OSError):
        await lifecycle.run(PiSessionConnector(path), binding())
    assert checkpoints.value == original
    assert {event.event_id for event in sink.records.values()} == {"root", "next"}
    sink.fail_event, sink.reject_event = None, "last"
    rejected = await lifecycle.run(PiSessionConnector(path), binding())
    assert rejected.status is ConnectorRunStatus.INCOMPLETE
    assert rejected.committed_checkpoint == first.committed_checkpoint
    assert checkpoints.value == original
    sink.reject_event = None
    restarted_store = Checkpoints()
    restarted_store.value = json.loads(json.dumps(checkpoints.value))
    restarted = ConnectorLifecycle(sink=sink, checkpoints=restarted_store)
    completed = await restarted.run(PiSessionConnector(path), binding())
    assert completed.status is ConnectorRunStatus.COMPLETE
    assert {event.event_id for event in sink.records.values()} == {"root", "next", "last"}
    assert not (await restarted.run(PiSessionConnector(path), binding())).items


async def test_rewrite_on_other_branch_is_rejected_before_new_writes(tmp_path):
    path = tmp_path / "session.jsonl"
    records = [message("root", None, "root"), message("left", "root", "left"), message("right", "root", "right")]
    path.write_bytes(archive(records))
    sink, checkpoints = Sink(), Checkpoints()
    lifecycle = ConnectorLifecycle(sink=sink, checkpoints=checkpoints)
    await lifecycle.run(PiSessionConnector(path, "left"), binding())
    original = deepcopy(checkpoints.value)
    records[1]["timestamp"] = "rewritten metadata"
    path.write_bytes(archive(records))
    with pytest.raises(ValueError, match="rewritten"):
        await lifecycle.run(PiSessionConnector(path, "right"), binding())
    assert checkpoints.value == original
    assert {event.event_id for event in sink.records.values()} == {"root", "left"}
    records[1].pop("timestamp")
    path.write_bytes(archive(records))
    assert len((await lifecycle.run(PiSessionConnector(path, "right"), binding())).items) == 1
