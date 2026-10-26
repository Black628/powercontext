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

import pytest
from pydantic import JsonValue

from powercontext.sources import (
    SourceDefinitionRegistry,
    SourceMaterialization,
    TextEvidence,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.pi_session import (
    PI_EVENT_SOURCE_DEFINITION,
    PiEventInput,
    PiEventSource,
    PiSessionInput,
    parse_pi_session,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


def _archive(
    records: list[dict[str, JsonValue]], *, session_id: str = "session", leaf_id: str | None = None
) -> PiSessionInput:
    header = {"type": "session", "version": 3, "id": session_id}
    return PiSessionInput(
        content="\n".join(json.dumps(record, ensure_ascii=False) for record in [header, *records]).encode(),
        leaf_id=leaf_id,
    )


def _message(event_id: str, parent_id: str | None, text: str) -> dict[str, JsonValue]:
    return {"type": "message", "id": event_id, "parentId": parent_id, "message": {"role": "user", "content": text}}


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ({"role": "user", "content": "  原文\n\t"}, "user:   原文\n\t"),
        (
            {"role": "assistant", "content": [{"type": "text", "text": ""}, {"type": "text", "text": "answer"}]},
            "assistant: \nanswer",
        ),
        (
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "not projected"},
                    {"type": "text", "text": "调用"},
                    {"type": "toolCall", "name": "read", "id": "call", "arguments": {"z": 1, "a": {"字": "值"}}},
                    {"type": "image", "data": "not projected"},
                ],
            },
            'assistant: 调用\n[tool_call read call] {"a":{"字":"值"},"z":1}',
        ),
        (
            {
                "role": "toolResult",
                "toolName": "read",
                "toolCallId": "call",
                "content": [{"type": "text", "text": " 结果\n"}],
            },
            "toolResult: [tool_result read call]  结果\n",
        ),
        (
            {"role": "bashExecution", "command": "echo literal", "output": "output\r\n", "content": []},
            "bashExecution: $ echo literal\noutput\r\n",
        ),
    ],
)
def test_visible_messages_preserve_exact_text_and_complete_records(
    message: dict[str, JsonValue], expected: str
) -> None:
    record: dict[str, JsonValue] = {
        "type": "message",
        "id": "event",
        "parentId": None,
        "message": message,
        "extra": {"retained": True},
    }
    (event,) = parse_pi_session(_archive([record]))
    assert event.content == expected
    assert event.record == record
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(event))
    assert isinstance(source, PiEventSource)
    assert source.materialization is SourceMaterialization.CAPTURED
    restored = PiEventSource.model_validate_json(source.model_dump_json())
    assert asyncio.run(registry.read(restored)) == event
    manifest = manifest_for_definition(PI_EVENT_SOURCE_DEFINITION)
    observation = project_source_for_transport(registry, restored)
    evidence = TextEvidence.model_validate(observation.projection(TEXT_EVIDENCE_PROJECTION_KEY))
    assert manifest.name == evidence.source_type == "pi-event"
    assert manifest.version == source.definition_version == "1"
    assert evidence.source_id == source.name
    assert evidence.content == expected
    assert evidence.metadata == {
        "session_id": "session",
        "event_id": "event",
        "parent_id": None,
        "event_type": "message",
        "role": event.role,
    }


def test_raw_branch_keeps_pre_compaction_history_and_control_parents() -> None:
    records: list[dict[str, JsonValue]] = [
        _message("root", None, "original"),
        _message("other", "root", "abandoned"),
        {
            "type": "compaction",
            "id": "compact",
            "parentId": "root",
            "summary": "summary",
            "firstKeptEntryId": "compact",
        },
        {"type": "model_change", "id": "model", "parentId": "compact"},
        {"type": "branch_summary", "id": "branch", "parentId": "model", "summary": "branch summary"},
        {"type": "context_edit", "id": "edit", "parentId": "branch", "targetId": "root", "replacement": None},
        {"type": "custom_message", "id": "custom", "parentId": "edit", "content": "extension text"},
        {"type": "label", "id": "label", "parentId": "custom", "label": "end"},
    ]
    events = parse_pi_session(_archive(records))
    assert [(event.event_id, event.parent_id) for event in events] == [
        ("root", None),
        ("compact", "root"),
        ("branch", "model"),
        ("custom", "edit"),
    ]
    assert [event.content for event in events] == [
        "user: original",
        "compaction: summary",
        "branch_summary: branch summary",
        "custom: extension text",
    ]
    assert [event.event_id for event in parse_pi_session(_archive(records, leaf_id="other"))] == ["root", "other"]


def test_bom_and_unicode_line_separators_remain_literal_content() -> None:
    value = _archive([_message("event", None, "first\u2028second\u0085third\u2029fourth")])
    value.content = b"\xef\xbb\xbf" + value.content.replace(b"\n", b"\r\n")
    assert parse_pi_session(value)[0].content == "user: first\u2028second\u0085third\u2029fourth"


@pytest.mark.parametrize(
    "content",
    ["", " \n", [{"type": "thinking", "thinking": "private"}], [{"type": "image", "data": "image"}]],
)
def test_events_without_visible_text_are_ignored(content: JsonValue) -> None:
    record: dict[str, JsonValue] = {
        "type": "message",
        "id": "event",
        "parentId": None,
        "message": {"role": "assistant", "content": content},
    }
    assert parse_pi_session(_archive([record])) == ()


@pytest.mark.parametrize(
    "node",
    [
        {"type": "label", "id": "node"},
        {"type": "label", "id": "node", "parentId": "missing"},
        {"type": "label", "id": "node", "parentId": "node"},
        {"type": "label", "id": "root", "parentId": None},
        {"type": "label", "id": "", "parentId": None},
        {"type": "label", "id": 2, "parentId": None},
        {"type": "label", "id": "node", "parentId": 1},
        {"type": "session", "id": "node", "parentId": None, "version": 3},
    ],
)
def test_invalid_nodes_on_unselected_branches_fail_before_returning_results(node: dict[str, JsonValue]) -> None:
    with pytest.raises(ValueError):
        parse_pi_session(_archive([_message("root", None, "valid"), node], leaf_id="root"))


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]",
        b"null",
        b"{}",
        b"\xff",
        b'{"type":"session","version":2,"id":"session"}',
        b'{"type":"session","version":3.0,"id":"session"}',
        b'{"type":"session","version":3,"id":""}',
        b'{"type":"session","version":3,"id":"session"}\n{bad}',
        b'{"type":"session","version":3,"id":"session"}\n{"type":"label","id":"a","parentId":null,"bad":NaN}',
    ],
)
def test_invalid_archive_is_rejected_atomically(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=raw))


def test_empty_archive_tree_is_valid_but_explicit_leaf_must_exist() -> None:
    assert parse_pi_session(_archive([])) == ()
    with pytest.raises(ValueError):
        parse_pi_session(_archive([], leaf_id="missing"))


def test_source_id_is_bounded_stable_and_unambiguous_for_long_provider_ids() -> None:
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    names = []
    for session_id, event_id in [("a:b", "c"), ("a", "b:c"), ("会话" * 300, "事件" * 300)]:
        (event,) = parse_pi_session(_archive([_message(event_id, None, "before")], session_id=session_id))
        name = asyncio.run(registry.resolve(event)).name
        assert 0 < len(name) <= 256
        (edited,) = parse_pi_session(_archive([_message(event_id, None, "after")], session_id=session_id))
        assert asyncio.run(registry.resolve(edited)).name == name
        names.append(name)
    assert len(set(names)) == len(names)


def test_large_tool_result_is_not_truncated_by_memory_entry_limits() -> None:
    body = "工具结果 " * 5000 + "END"
    record: dict[str, JsonValue] = {
        "type": "message",
        "id": "result",
        "parentId": None,
        "message": {"role": "toolResult", "toolName": "read", "toolCallId": "call", "content": body},
    }
    (event,) = parse_pi_session(_archive([record]))
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(event))
    evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
    assert evidence.content == f"toolResult: [tool_result read call] {body}"
    assert len(evidence.content.encode()) > 8192
    restored = asyncio.run(registry.read(source))
    assert isinstance(restored, PiEventInput)
    assert restored.record == record
