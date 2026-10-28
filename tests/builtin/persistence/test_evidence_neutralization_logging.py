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

"""Neutral evidence facts report a bounded, content-free operational reason."""

from __future__ import annotations

import json
import logging

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence.atomic_memory_evidence import AtomicMemoryEvidenceRepository
from powercontext.server.logging import JsonFormatter, OperationalContextFilter


def _neutral_records(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "event", None) == "atomic_memory.evidence.neutral"]


def test_undecodable_snapshot_reports_decode_failed(caplog) -> None:
    reference = ArtifactRef(family="atomic-memory", artifact_id="memory", revision=1)

    with caplog.at_level(logging.DEBUG, logger="powercontext.sources.evidence"):
        assert AtomicMemoryEvidenceRepository._decode("scope", reference, b"\xffnot-json") is None

    record = _neutral_records(caplog)[-1]
    assert record.getMessage() == "Memory evidence resolved to neutral"
    assert (
        getattr(record, "operation", None),
        getattr(record, "unit", None),
        getattr(record, "outcome", None),
        getattr(record, "reason", None),
    ) == (
        "atomic_memory.evidence",
        "declaration",
        "neutral",
        "decode_failed",
    )


def test_neutral_reason_is_serialized_without_evidence_content(caplog) -> None:
    reference = ArtifactRef(family="atomic-memory", artifact_id="memory", revision=1)

    with caplog.at_level(logging.DEBUG, logger="powercontext.sources.evidence"):
        AtomicMemoryEvidenceRepository._decode("scope", reference, b"\xffnot-json")

    record = _neutral_records(caplog)[-1]
    OperationalContextFilter().filter(record)
    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "atomic_memory.evidence.neutral"
    assert (payload["operation"], payload["unit"], payload["outcome"], payload["reason"]) == (
        "atomic_memory.evidence",
        "declaration",
        "neutral",
        "decode_failed",
    )
    assert set(payload) <= {
        "timestamp",
        "level",
        "logger",
        "message",
        "event",
        "operation",
        "unit",
        "outcome",
        "reason",
        "request_id",
        "trace_id",
        "span_id",
        "service_name",
        "service_version",
        "service_role",
        "service_instance_id",
    }
