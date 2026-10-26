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

"""Capture immutable Pi v3 branch history with lifecycle-owned checkpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Literal, Never

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

_JSON_RECORD = TypeAdapter(dict[str, JsonValue])


class PiSessionInput(BaseModel):
    """Complete UTF-8 JSONL archive and an optional selected terminal node."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One original visible event, including its complete native record."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_event(self) -> PiEventInput:
        _require_id(self.session_id)
        _require_id(self.event_id)
        visible = _visible_event(self.record)
        if (
            self.record.get("id") != self.event_id
            or "parentId" not in self.record
            or self.record["parentId"] != self.parent_id
            or self.record.get("type") != self.event_type
            or visible != (self.role, self.content)
        ):
            raise ValueError("Pi event fields must match the complete original record")  # noqa: TRY003
        if self.parent_id is not None:
            _require_id(self.parent_id)
        return self


class PiEventSource(Source, PiEventInput):
    """Captured event whose identity never depends on its text or branch tip."""

    definition_version: Literal["1"] = "1"
    materialization: Literal[SourceMaterialization.CAPTURED] = SourceMaterialization.CAPTURED

    @model_validator(mode="after")
    def validate_identity(self) -> PiEventSource:
        if self.name != _event_source_id(self.session_id, self.event_id):
            raise ValueError("Pi Source name must match its session and event identity")  # noqa: TRY003
        return self


def _require_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Pi identities must be non-empty strings")  # noqa: TRY003
    return value


def _canonical_json(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _event_source_id(session_id: str, event_id: str) -> str:
    # A JSON pair is unambiguous even for delimiters and long provider IDs.
    identity = _canonical_json([_require_id(session_id), _require_id(event_id)])
    return f"pi:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _visible_text(value: JsonValue, *, tool_calls: bool = False) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise ValueError("Pi message content must be a string or content-block array")  # noqa: TRY003, TRY004
    parts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            raise ValueError("Pi content blocks must be JSON objects")  # noqa: TRY003, TRY004
        if block.get("type") == "text":
            text = block.get("text")
            if not isinstance(text, str):
                raise ValueError("Pi text blocks must contain a string")  # noqa: TRY003
            parts.append(text)
        elif tool_calls and block.get("type") == "toolCall":
            name = _require_id(block.get("name"))
            call_id = _require_id(block.get("id"))
            arguments = block.get("arguments")
            if not isinstance(arguments, dict):
                raise ValueError("Pi tool-call arguments must be a JSON object")  # noqa: TRY003
            parts.append(f"[tool_call {name} {call_id}] {_canonical_json(arguments)}")
    return "\n".join(parts)


def _message_body(message: JsonValue) -> tuple[str, str] | None:
    if not isinstance(message, dict):
        raise ValueError("Pi message events must contain a message object")  # noqa: TRY003, TRY004
    role = _require_id(message.get("role"))
    if role == "system":
        return None
    if role == "bashExecution":
        command, output = message.get("command"), message.get("output")
        if not isinstance(command, str) or not isinstance(output, str):
            raise ValueError("Pi bash events must contain command and output strings")  # noqa: TRY003
        return role, f"$ {command}\n{output}"
    body = _visible_text(message.get("content"), tool_calls=True)
    if not body.strip():
        return None
    if role == "toolResult":
        name = _require_id(message.get("toolName"))
        call_id = _require_id(message.get("toolCallId"))
        body = f"[tool_result {name} {call_id}] {body}"
    return role, body


def _visible_event(record: dict[str, JsonValue]) -> tuple[str, str] | None:
    event_type = record.get("type")
    if event_type == "message":
        rendered = _message_body(record.get("message"))
        if rendered is None:
            return None
        role, body = rendered
    elif event_type in ("compaction", "branch_summary"):
        role = str(event_type)
        body = record.get("summary")
        if not isinstance(body, str):
            raise ValueError("Pi summary events must contain a summary string")  # noqa: TRY003
    elif event_type == "custom_message":
        role = "custom"
        body = _visible_text(record.get("content"))
    else:
        return None
    return (role, f"{role}: {body}") if body.strip() else None


def _reject_json_constant(_value: str) -> Never:
    raise ValueError("Pi JSON records must not contain non-finite numbers")  # noqa: TRY003


def _parse_archive(value: PiSessionInput) -> tuple[str, dict[str, dict[str, JsonValue]], str | None]:
    # JSONL separators are LF/CRLF; Unicode separators inside JSON strings are data.
    records = (
        _JSON_RECORD.validate_python(json.loads(line, parse_constant=_reject_json_constant))
        for line in value.content.decode("utf-8-sig").split("\n")
        if line.strip()
    )
    header = next(records, None)
    if (
        header is None
        or header.get("type") != "session"
        or type(header.get("version")) is not int
        or header["version"] != 3
    ):
        raise ValueError("Pi archives must start with a version 3 session header")  # noqa: TRY003
    session_id = _require_id(header.get("id"))
    nodes: dict[str, dict[str, JsonValue]] = {}
    last_id: str | None = None
    for record in records:
        event_id = _require_id(record.get("id"))
        event_type = _require_id(record.get("type"))
        if event_type == "session" or event_id in nodes or "parentId" not in record:
            raise ValueError("Pi nodes must have unique IDs and explicit parents")  # noqa: TRY003
        parent = record["parentId"]
        if parent is not None and _require_id(parent) not in nodes:
            raise ValueError("Pi parents must refer to an earlier node")  # noqa: TRY003
        nodes[event_id] = record
        last_id = event_id
    leaf = value.leaf_id if value.leaf_id is not None else last_id
    if leaf is not None and leaf not in nodes:
        raise ValueError("Pi selected leaf must exist in the archive")  # noqa: TRY003
    return session_id, nodes, leaf


def _branch_events(
    session_id: str, nodes: dict[str, dict[str, JsonValue]], leaf: str | None
) -> tuple[PiEventInput, ...]:
    chain: list[dict[str, JsonValue]] = []
    while leaf is not None:
        record = nodes[leaf]
        chain.append(record)
        parent = record["parentId"]
        leaf = _require_id(parent) if parent is not None else None
    events: list[PiEventInput] = []
    for record in reversed(chain):
        visible = _visible_event(record)
        if visible is not None:
            role, content = visible
            events.append(
                PiEventInput(
                    session_id=session_id,
                    event_id=_require_id(record["id"]),
                    parent_id=_require_id(record["parentId"]) if record["parentId"] is not None else None,
                    event_type=_require_id(record["type"]),
                    role=role,
                    content=content,
                    record=record,
                )
            )
    return tuple(events)


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Validate the whole graph, then return the raw visible root-to-leaf history."""

    return _branch_events(*_parse_archive(value))


class _PiEventAdapter:
    input_class = PiEventInput
    name = "pi-event"
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        return PiEventSource(name=_event_source_id(value.session_id, value.event_id), **value.model_dump())

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput.model_validate(source.model_dump())


class _PiEventTextEvidence:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = AdapterSourceDefinition(
    _PiEventAdapter(), projections=(_PiEventTextEvidence(),)
)


class _PiCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1"] = "1"
    session_id: str
    imported: dict[str, str] = Field(default_factory=dict)


def _record_digest(record: dict[str, JsonValue]) -> str:
    return hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()


class PiSessionConnector:
    """Import newly selected events while preserving checksums across branches."""

    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        raw = await asyncio.to_thread(self.path.read_bytes)
        session_id, nodes, leaf = _parse_archive(PiSessionInput(content=raw, leaf_id=self.leaf_id))
        events = _branch_events(session_id, nodes, leaf)
        previous = (
            _PiCheckpoint(session_id=session_id)
            if session.checkpoint is None
            else _PiCheckpoint.model_validate(session.checkpoint)
        )
        if previous.session_id != session_id:
            raise ValueError("Pi checkpoint belongs to another session")  # noqa: TRY003
        # Check every imported branch before writing anything, including branches
        # that are not selected in this run. Source evidence is append-only.
        for event_id, digest in previous.imported.items():
            if event_id not in nodes or _record_digest(nodes[event_id]) != digest:
                raise ValueError("Previously imported Pi event was removed or rewritten")  # noqa: TRY003
        imported = dict(previous.imported)
        for event in events:
            if event.event_id in imported:
                continue
            outcome = await session.submit(_event_source_id(session_id, event.event_id), "pi-event", event)
            if outcome.status is not ConnectorSubmissionStatus.ACCEPTED:
                return ConnectorRunCompletion(status=ConnectorRunStatus.INCOMPLETE, checkpoint=session.checkpoint)
            imported[event.event_id] = _record_digest(event.record)
        checkpoint = _PiCheckpoint(session_id=session_id, imported=imported)
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=checkpoint.model_dump(mode="json"))


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
]
