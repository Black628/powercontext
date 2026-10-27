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

"""Captured Pi v3 branch evidence and durable incremental acquisition."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, JsonValue

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
    Source,
    SourceMaterialization,
    TextEvidence,
)


class PiSessionInput(BaseModel):
    """A complete UTF-8 JSONL snapshot and an optional selected leaf."""

    model_config = ConfigDict(frozen=True, strict=True)

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event, including its full original JSON record."""

    model_config = ConfigDict(frozen=True, strict=True)

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _identity(session_id: str, event_id: str) -> str:
    # A JSON pair distinguishes identities even when IDs contain delimiters.
    encoded = _canonical([session_id, event_id]).encode("utf-8")
    return "pi-event:" + hashlib.sha256(encoded).hexdigest()


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Non-JSON numeric constant: {value}")  # noqa: TRY003


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Pi {field} must be a non-empty string")  # noqa: TRY003
    return value


def _text(content: object, *, tool_calls: bool = False) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif tool_calls and block.get("type") == "toolCall":
            name = _required_string(block.get("name"), "tool name")
            call_id = _required_string(block.get("id"), "tool call ID")
            arguments = _canonical(block.get("arguments", {}))
            parts.append(f"[tool_call {name} {call_id}] {arguments}")
    return "\n".join(parts)


def _message_body(message: object) -> tuple[str, str]:
    if not isinstance(message, dict):
        raise ValueError("Pi message must contain a message object")  # noqa: TRY003, TRY004
    role = _required_string(message.get("role"), "message role")
    if role == "bashExecution":
        command, output = message.get("command", ""), message.get("output", "")
        if not isinstance(command, str) or not isinstance(output, str):
            raise ValueError("Pi bash command and output must be strings")  # noqa: TRY003
        return role, f"$ {command}\n{output}" if command or output else ""
    body = _text(message.get("content"), tool_calls=True)
    if role == "toolResult" and body:
        name = _required_string(message.get("toolName"), "tool result name")
        call_id = _required_string(message.get("toolCallId"), "tool result call ID")
        body = f"[tool_result {name} {call_id}] {body}"
    return role, body


def _event(session_id: str, record: dict[str, Any]) -> PiEventInput | None:
    event_type = record["type"]
    if event_type == "message":
        role, body = _message_body(record.get("message"))
    elif event_type in {"compaction", "branch_summary"}:
        role, body = event_type, record.get("summary", "")
        if not isinstance(body, str):
            raise ValueError("Pi summary must be a string")  # noqa: TRY003
    elif event_type == "custom_message":
        role, body = "custom", _text(record.get("content"))
    else:
        return None
    if not body:
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=record["id"],
        parent_id=record["parentId"],
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _archive(content: bytes) -> tuple[str, dict[str, dict[str, Any]], dict[str, PiEventInput]]:
    records = [
        json.loads(line, parse_constant=_invalid_constant)
        for line in content.decode("utf-8-sig").split("\n")
        if line.strip()
    ]
    if not records or not isinstance(records[0], dict):
        raise ValueError("Pi archive must start with a session header")  # noqa: TRY003
    header = records[0]
    if header.get("type") != "session" or type(header.get("version")) is not int or header["version"] != 3:
        raise ValueError("Pi archive requires a version 3 session header")  # noqa: TRY003
    session_id = _required_string(header.get("id"), "session ID")
    nodes: dict[str, dict[str, Any]] = {}
    events = {}
    for record in records[1:]:
        if not isinstance(record, dict):
            raise ValueError("Pi node must be an object")  # noqa: TRY003, TRY004
        event_id = _required_string(record.get("id"), "event ID")
        _required_string(record.get("type"), "event type")
        if event_id in nodes or "parentId" not in record:
            raise ValueError("Pi node has a duplicate ID or missing parentId")  # noqa: TRY003
        parent_id = record["parentId"]
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in nodes):
            raise ValueError("Pi parentId must refer to an earlier node or be null")  # noqa: TRY003
        # Earlier-only parents also reject self references and all cycles.
        nodes[event_id] = record
        event = _event(session_id, record)
        if event is not None:
            events[event_id] = event
    return session_id, nodes, events


def _branch(
    nodes: dict[str, dict[str, Any]], events: dict[str, PiEventInput], leaf_id: str | None
) -> tuple[PiEventInput, ...]:
    if leaf_id is None:
        if not nodes:
            return ()
        leaf_id = next(reversed(nodes))
    if leaf_id not in nodes:
        raise ValueError("Pi selected leaf does not exist")  # noqa: TRY003
    selected = []
    while leaf_id is not None:
        if leaf_id in events:
            selected.append(events[leaf_id])
        leaf_id = nodes[leaf_id]["parentId"]
    return tuple(reversed(selected))


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Validate the whole archive, then project the complete selected path."""

    _, nodes, events = _archive(value.content)
    return _branch(nodes, events, value.leaf_id)


class _PiEventSource(Source):
    event: PiEventInput


class _PiEventAdapter:
    input_class: type[PiEventInput] = PiEventInput
    name = "pi-event"
    source_class: type[_PiEventSource] = _PiEventSource

    async def resolve(self, value: PiEventInput, /) -> _PiEventSource:
        return _PiEventSource(
            name=_identity(value.session_id, value.event_id),
            materialization=SourceMaterialization.CAPTURED,
            event=value.model_copy(deep=True),
        )

    async def read(self, source: _PiEventSource, /) -> PiEventInput:
        return source.event.model_copy(deep=True)


class _PiTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[_PiEventSource] = _PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: _PiEventSource, /) -> TextEvidence:
        event = source.event
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=event.content,
            metadata={
                "session_id": event.session_id,
                "event_id": event.event_id,
                "parent_id": event.parent_id,
                "event_type": event.event_type,
                "role": event.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    _PiEventAdapter(), version="1", projections=(_PiTextEvidenceProjection(),)
)


def _checkpoint(value: JsonValue | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or type(value.get("version")) is not int or value.get("version") != 1:
        raise ValueError("Unsupported Pi checkpoint")  # noqa: TRY003
    imported = value.get("events")
    if not isinstance(imported, dict) or not all(
        isinstance(key, str) and isinstance(digest, str) for key, digest in imported.items()
    ):
        raise ValueError("Invalid Pi checkpoint event digests")  # noqa: TRY003
    return dict(cast(dict[str, str], imported))


class PiSessionConnector:
    """Import selected branch events with lifecycle-owned durable progress."""

    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        session_id, nodes, events = _archive(self.path.read_bytes())
        selected = _branch(nodes, events, self.leaf_id)
        imported = _checkpoint(session.checkpoint)
        digests = {}
        # Check previously captured events on every branch before any writes.
        for event_id, record in nodes.items():
            identity = _identity(session_id, event_id)
            digest = hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()
            digests[identity] = digest
            if identity in imported and imported[identity] != digest:
                raise ValueError("A previously imported Pi event was rewritten")  # noqa: TRY003
        for event in selected:
            identity = _identity(event.session_id, event.event_id)
            if identity not in imported:
                outcome = await session.submit(identity, "pi-event", event)
                if outcome.status is not ConnectorSubmissionStatus.ACCEPTED:
                    return ConnectorRunCompletion(status=ConnectorRunStatus.INCOMPLETE, checkpoint=session.checkpoint)
                imported[identity] = digests[identity]
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint={"version": 1, "events": imported})


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
]
