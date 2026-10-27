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

"""Pi v3 session history: the selected branch as immutable event Sources."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

SOURCE_TYPE = "pi-event"
CONNECTOR_NAME = "pi-session-history"
DEFINITION_VERSION = "1"
SESSION_TYPE = "session"
SESSION_VERSION = 3
_BOM = "\ufeff"
_CHECKPOINT_VERSION = 1

# Event types that never carry visible evidence of their own.
_CONTROL_TYPES = frozenset(
    {
        "model_change",
        "thinking_level_change",
        "label",
        "session",
        "toolCall",
        "toolResult",
    }
)


class PiSessionInput(BaseModel):
    """Raw Pi session bytes plus the branch leaf the caller wants captured."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on the selected branch, with its complete source record."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


class PiEventSource(Source):
    """A captured, immutable projection of one Pi session event."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]

    @model_validator(mode="after")
    def validate_identity(self) -> PiEventSource:
        if self.name != self.source_id:
            raise ValueError("Pi event Source name must equal its source_id")  # noqa: TRY003
        return self


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Derive the stable identity of one event from its session and event ids."""

    for field, value in (("session_id", session_id), ("event_id", event_id)):
        if not isinstance(value, str) or not value or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")  # noqa: TRY003
    identity = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        digest = hashlib.sha256(f"{session_id}\x00{event_id}".encode()).hexdigest()
        identity = f"pi:sha256:{digest}"
    return identity


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Return the visible events of the selected branch, ordered root to leaf."""

    session_id, records = _parse_archive(value.content)
    by_id = {record["id"]: record for record in records}

    leaf_id = value.leaf_id
    if leaf_id is None:
        leaf_id = records[-1]["id"] if records else None
    elif leaf_id not in by_id:
        raise ValueError(f"leaf {leaf_id!r} is not part of the session")  # noqa: TRY003

    branch: list[dict[str, Any]] = []
    cursor = leaf_id
    while cursor is not None:
        record = by_id[cursor]
        branch.append(record)
        cursor = record.get("parentId")
    branch.reverse()

    events: list[PiEventInput] = []
    for record in branch:
        rendered = _render_event(record)
        if rendered is None:
            continue
        role, content = rendered
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=record["id"],
                parent_id=record.get("parentId"),
                event_type=record["type"],
                role=role,
                content=content,
                record=record,
            )
        )
    return tuple(events)


def _parse_archive(content: bytes) -> tuple[str, list[dict[str, Any]]]:
    text = content.decode("utf-8")
    if text.startswith(_BOM):
        text = text[1:]
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Pi session archive must not be empty")  # noqa: TRY003

    header = _load_object(lines[0], "session header")
    if header.get("type") != SESSION_TYPE:
        raise ValueError("first record must declare type 'session'")  # noqa: TRY003
    if header.get("version") != SESSION_VERSION:
        raise ValueError(f"session version must be {SESSION_VERSION}")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session id must be a non-empty string")  # noqa: TRY003

    records: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for line in lines[1:]:
        record = _load_object(line, "session record")
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("every record must declare a non-empty string id")  # noqa: TRY003
        if event_id in seen:
            raise ValueError(f"duplicate record id {event_id!r}")  # noqa: TRY003
        parent_id = record.get("parentId")
        if parent_id is not None:
            if not isinstance(parent_id, str) or parent_id not in seen:
                raise ValueError(f"record {event_id!r} references unknown parent {parent_id!r}")  # noqa: TRY003
        seen[event_id] = record
        records.append(record)
    return session_id, records


def _load_object(line: str, what: str) -> dict[str, Any]:
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError(f"{what} must be a JSON object")  # noqa: TRY003
    event_type = value.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise ValueError(f"{what} must declare a string type")  # noqa: TRY003
    return value


def _render_event(record: dict[str, Any]) -> tuple[str, str] | None:
    event_type = record["type"]
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            raise ValueError(f"message record {record['id']!r} must contain a message object")  # noqa: TRY003
        role = message.get("role")
        if not isinstance(role, str) or not role:
            raise ValueError(f"message record {record['id']!r} must declare a role")  # noqa: TRY003
        body = _message_body(role, message)
        if not body.strip():
            return None
        return role, f"{role}: {body}"
    if event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        body = summary if isinstance(summary, str) else ""
        if not body.strip():
            return None
        return event_type, f"{event_type}: {body}"
    if event_type == "custom_message":
        body = _content_text(record.get("content"))
        if not body.strip():
            return None
        return "custom", f"custom: {body}"
    if event_type in _CONTROL_TYPES:
        return None
    return None


def _message_body(role: str, message: dict[str, Any]) -> str:
    if role == "bashExecution":
        command = message.get("command")
        output = message.get("output")
        head = f"$ {command}" if isinstance(command, str) else "$"
        if isinstance(output, str) and output:
            return f"{head}\n{output}"
        return head
    if role == "toolResult":
        name = message.get("toolName")
        call_id = message.get("toolCallId")
        return f"[tool_result {name} {call_id}] {_content_text(message.get('content'))}"
    return _content_text(message.get("content"))


def _content_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif block_type == "toolCall":
            parts.append(_tool_call_text(block))
    return "\n".join(parts)


def _tool_call_text(block: dict[str, Any]) -> str:
    name = block.get("name")
    call_id = block.get("id")
    arguments = block.get("arguments")
    if arguments is None:
        arguments = block.get("input")
    encoded = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"[tool_call {name} {call_id}] {encoded}"


class _PiEventAdapter:
    """Resolve parsed events into Sources and read them back without loss."""

    name = SOURCE_TYPE
    input_class = PiEventInput
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            definition_version=DEFINITION_VERSION,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class _PiEventTextEvidenceProjection:
    """Standard ``powercontext.text-evidence`` view of one session event."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=SOURCE_TYPE,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION: AdapterSourceDefinition[PiEventInput, PiEventSource, PiEventInput] = AdapterSourceDefinition(
    adapter=_PiEventAdapter(),
    version=DEFINITION_VERSION,
    projections=(_PiEventTextEvidenceProjection(),),
)


def _record_digest(record: dict[str, Any]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class PiSessionConnector:
    """Import only the not-yet-imported visible events of one Pi branch."""

    name = CONNECTOR_NAME
    version = DEFINITION_VERSION
    source_definitions = frozenset({SOURCE_TYPE})

    def __init__(self, path: Path | str, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        events = parse_pi_session(PiSessionInput(content=self.path.read_bytes(), leaf_id=self.leaf_id))
        previous = session.checkpoint if isinstance(session.checkpoint, dict) else {}
        imported = previous.get("events")
        imported = dict(imported) if isinstance(imported, dict) else {}

        # Validate the whole branch before persisting anything so a rewritten
        # record cannot leave a half-imported branch behind.
        for event in events:
            digest = _record_digest(event.record)
            known = imported.get(event.event_id)
            if known is not None and known != digest:
                raise ValueError(f"event {event.event_id!r} was rewritten after it was imported")  # noqa: TRY003

        for event in events:
            digest = _record_digest(event.record)
            if event.event_id in imported:
                continue
            await session.submit(event.event_id, SOURCE_TYPE, event)
            imported[event.event_id] = digest

        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"version": _CHECKPOINT_VERSION, "events": imported},
        )


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
