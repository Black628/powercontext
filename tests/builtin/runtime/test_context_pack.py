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

import base64
import hashlib
import json
import subprocess
import sys

import pytest

from powercontext.builtin.runtime.context_pack import (
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)


@pytest.mark.parametrize("size", [0, 1, 50, 500, 6000])
def test_full_pack_has_canonical_digest_and_self_inclusive_size(size: int) -> None:
    items = (b"x" * size, "海洋🌊".encode())
    pack = ContextPack.build(items)
    payload = json.loads(pack.to_bytes())
    unsigned = {
        "schema": "powercontext.context-pack.v1",
        "items": [base64.b64encode(item).decode("ascii") for item in items],
    }
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    assert payload == {
        **unsigned,
        "digest": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "byte_count": len(pack.to_bytes()),
    }
    assert pack.to_bytes() == json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    assert ContextPack.from_bytes(pack.to_bytes()) == pack
    assert pack.byte_count == len(pack.to_bytes())


def test_pack_identity_retains_chunk_boundaries_and_arbitrary_bytes() -> None:
    assert ContextPack.build((b"ab", b"c")).digest != ContextPack.build((b"a", b"bc")).digest
    binary = ContextPack.build((b"\xff\x00", b""))
    assert ContextPack.from_bytes(binary.to_bytes()).items == binary.items


@pytest.mark.parametrize("chunk_bytes", [4, 5, 7, 512])
def test_chunking_preserves_exact_utf8_and_line_endings(chunk_bytes: int) -> None:
    text = "first\r\n海洋🌊 e\u0301\u2028last\n"
    pack = ContextPack.from_content(text, chunk_bytes=chunk_bytes)
    assert b"".join(pack.items) == text.encode()
    assert all(0 < len(item) <= chunk_bytes for item in pack.items)
    for item in pack.items:
        item.decode("utf-8")
    assert pack == ContextPack.from_content(text.encode(), chunk_bytes=chunk_bytes)
    assert pack == ContextPack.from_content(text, chunk_bytes=chunk_bytes)


@pytest.mark.parametrize("chunk_bytes", [0, -1, True, 2.5, "512"])
def test_chunking_rejects_invalid_size(chunk_bytes) -> None:
    with pytest.raises(ContextPackError):
        ContextPack.from_content("", chunk_bytes=chunk_bytes)


@pytest.mark.parametrize("content,limit", [("海", 2), ("🌊", 3), ("a海", 1), (b"\xff", 512), ("\ud800", 512)])
def test_chunking_rejects_invalid_utf8_or_impossible_boundary(content, limit: int) -> None:
    with pytest.raises(ContextPackError):
        ContextPack.from_content(content, chunk_bytes=limit)


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "other"},
        {"extra": None},
        {"digest": "sha256:" + "0" * 64},
        {"digest": "SHA256:" + "a" * 64},
        {"digest": None},
        {"byte_count": True},
        {"byte_count": 1.5},
        {"byte_count": "200"},
        {"byte_count": -1},
        {"items": "YQ=="},
        {"items": [1]},
        {"items": ["YQ==\n"]},
        {"items": ["YR=="]},
        {"items": ["YQ==="]},
        {"items": ["-w=="]},
        {"items": ["海洋"]},
    ],
)
def test_pack_rejects_invalid_or_tampered_wire_fields(change: dict[str, object]) -> None:
    payload = json.loads(ContextPack.build((b"a",)).to_bytes())
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps(payload | change).encode())


@pytest.mark.parametrize("field", ["schema", "items", "digest", "byte_count"])
def test_full_pack_requires_all_wire_fields(field: str) -> None:
    payload = json.loads(ContextPack.build(()).to_bytes())
    del payload[field]
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps(payload).encode())


@pytest.mark.parametrize("wire", [b"", b"\xff", b"null", b"[]", b"{}{}", b'{"schema":NaN}', b"1" * 5000])
def test_malformed_wire_errors_have_the_public_error_type(wire: bytes) -> None:
    for decode in (ContextPack.from_bytes, ContextPackDelta.from_bytes):
        with pytest.raises(ContextPackError):
            decode(wire)


def test_duplicate_json_fields_are_rejected() -> None:
    pack = ContextPack.build((b"a",))
    wire = pack.to_bytes().replace(b'{"byte_count":', b'{"byte_count":0,"byte_count":', 1)
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(wire)


@pytest.mark.parametrize("target_items", [(), (b"a",), (b"a", b"b"), (b"a", b"c"), (b"x", b"b")])
def test_serialized_delta_reconstructs_exact_target(target_items: tuple[bytes, ...]) -> None:
    base = ContextPack.build((b"a", b"b"))
    target = ContextPack.build(target_items)
    delta = ContextPackDelta.between(base, target)
    payload = json.loads(delta.to_bytes())
    assert set(payload) == {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
    assert delta.byte_count == len(delta.to_bytes())
    assert delta.to_bytes() == json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    result = apply_context_pack_delta(
        ContextPack.from_bytes(base.to_bytes()), ContextPackDelta.from_bytes(delta.to_bytes())
    )
    assert result == target
    assert result.to_bytes() == target.to_bytes()


@pytest.mark.parametrize("prefix", [0, 1, 2])
def test_receiver_accepts_every_valid_common_prefix(prefix: int) -> None:
    base = ContextPack.build((b"a", b"b", b"old"))
    target = ContextPack.build((b"a", b"b", b"new"))
    wire = json.dumps({
        "schema": "powercontext.context-pack-delta.v1",
        "base_digest": base.digest,
        "target_digest": target.digest,
        "prefix_items": prefix,
        "appended": [base64.b64encode(item).decode() for item in target.items[prefix:]],
    }).encode()
    assert apply_context_pack_delta(base, ContextPackDelta.from_bytes(wire)) == target


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "other"},
        {"base_digest": None},
        {"target_digest": "invalid"},
        {"prefix_items": True},
        {"prefix_items": 0.0},
        {"prefix_items": -1},
        {"appended": [False]},
        {"appended": ["YR=="]},
        {"appended": None},
        {"byte_count": 100},
    ],
)
def test_delta_rejects_invalid_wire_fields(change: dict[str, object]) -> None:
    base = ContextPack.build((b"a",))
    payload = json.loads(ContextPackDelta.between(base, base).to_bytes())
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps(payload | change).encode())


@pytest.mark.parametrize("field", ["schema", "base_digest", "target_digest", "prefix_items", "appended"])
def test_delta_requires_all_wire_fields(field: str) -> None:
    base = ContextPack.build(())
    payload = json.loads(ContextPackDelta.between(base, base).to_bytes())
    del payload[field]
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps(payload).encode())


def test_applying_delta_validates_base_bounds_and_target() -> None:
    base = ContextPack.build((b"a",))
    other = ContextPack.build((b"b",))
    for delta in (
        ContextPackDelta(other.digest, base.digest, 0, base.items),
        ContextPackDelta(base.digest, base.digest, 2, ()),
        ContextPackDelta(base.digest, other.digest, 1, ()),
    ):
        with pytest.raises(ContextPackError):
            apply_context_pack_delta(base, delta)


def test_independent_receiver_process_reconstructs_using_only_wire_bytes() -> None:
    base = ContextPack.from_content("original\r\n海洋🌊\n")
    target = ContextPack.from_content("original\r\n海洋🌊\nappended\n")
    delta = ContextPackDelta.between(base, target)
    script = """
import json, sys
from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, apply_context_pack_delta
base, delta = json.loads(sys.stdin.buffer.read())
result = apply_context_pack_delta(ContextPack.from_bytes(base.encode()), ContextPackDelta.from_bytes(delta.encode()))
sys.stdout.buffer.write(result.to_bytes())
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps([base.to_bytes().decode(), delta.to_bytes().decode()]).encode(),
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert result.stdout == target.to_bytes()
