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

"""Canonical, self-validating context packets and stateless prefix deltas."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Never

_PACK_SCHEMA = "powercontext.context-pack.v1"
_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"


class ContextPackError(ValueError):
    """A context packet, delta, or UTF-8 chunk violates its protocol."""


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _validate_digest(value: object) -> None:
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ContextPackError("Context digests must use sha256:<lowercase hex>")  # noqa: TRY003


def _validate_items(items: tuple[bytes, ...]) -> None:
    if not isinstance(items, tuple) or any(not isinstance(item, bytes) for item in items):
        raise ContextPackError("Context chunks must be a tuple of bytes")  # noqa: TRY003


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("Wire chunks must be an array of Base64 strings")  # noqa: TRY003
    items: list[bytes] = []
    for encoded in value:
        if not isinstance(encoded, str):
            raise ContextPackError("Wire chunks must be Base64 strings")  # noqa: TRY003
        try:
            item = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeError, binascii.Error) as error:
            raise ContextPackError("Invalid Base64 context chunk") from error  # noqa: TRY003
        if base64.b64encode(item).decode("ascii") != encoded:
            raise ContextPackError("Context chunks must use canonical standard Base64")  # noqa: TRY003
        items.append(item)
    return tuple(items)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContextPackError("Duplicate context packet field")  # noqa: TRY003
        result[key] = value
    return result


def _reject_constant(_value: str) -> Never:
    raise ContextPackError("Non-finite JSON numbers are not supported")  # noqa: TRY003


def _decode_wire(value: bytes, schema: str, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, bytes):
        raise ContextPackError("Context wire data must be UTF-8 bytes")  # noqa: TRY003
    try:
        payload = json.loads(value.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        raise ContextPackError("Invalid context packet JSON") from error  # noqa: TRY003
    if not isinstance(payload, dict) or payload.keys() != fields or payload["schema"] != schema:
        raise ContextPackError("Unexpected context packet fields or schema")  # noqa: TRY003
    return payload


@dataclass(frozen=True, slots=True)
class ContextPack:
    """An immutable complete packet; its identity includes exact chunk boundaries."""

    items: tuple[bytes, ...]
    digest: str = field(init=False)
    byte_count: int = field(init=False)
    _wire: bytes = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_items(self.items)
        unsigned = {"schema": _PACK_SCHEMA, "items": _encode_items(self.items)}
        digest = "sha256:" + hashlib.sha256(_json_bytes(unsigned)).hexdigest()
        payload = {**unsigned, "digest": digest, "byte_count": 0}
        # byte_count counts its own decimal representation. Iterate to its fixed point.
        while True:
            wire = _json_bytes(payload)
            if payload["byte_count"] == len(wire):
                break
            payload["byte_count"] = len(wire)
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "byte_count", len(wire))
        object.__setattr__(self, "_wire", wire)

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        """Capture the exact chunks without normalizing or reordering their bytes."""

        return cls(items)

    @classmethod
    def from_content(cls, content: str | bytes, *, chunk_bytes: int = 512) -> ContextPack:
        """Split at UTF-8 boundaries, preferring line ends within the byte ceiling."""

        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        try:
            if isinstance(content, str):
                raw = content.encode("utf-8")
            elif isinstance(content, bytes):
                content.decode("utf-8")
                raw = content
            else:
                raise ContextPackError("Context content must be text or UTF-8 bytes")  # noqa: TRY003
        except UnicodeError as error:
            raise ContextPackError("Context content must be valid UTF-8") from error  # noqa: TRY003
        items: list[bytes] = []
        start = 0
        while start < len(raw):
            end = min(start + chunk_bytes, len(raw))
            while end < len(raw) and raw[end] & 0xC0 == 0x80:
                end -= 1
            if end <= start:
                raise ContextPackError("chunk_bytes cannot hold the next UTF-8 character")  # noqa: TRY003
            newline = raw.rfind(b"\n", start, end)
            if newline >= start:
                end = newline + 1
            items.append(raw[start:end])
            start = end
        return cls.build(tuple(items))

    def to_bytes(self) -> bytes:
        """Return sorted compact UTF-8 JSON, including the self-sized byte count."""

        return self._wire

    @classmethod
    def from_bytes(cls, value: bytes) -> ContextPack:
        """Decode and validate the declaration against its complete chunk data."""

        payload = _decode_wire(value, _PACK_SCHEMA, {"schema", "items", "digest", "byte_count"})
        _validate_digest(payload["digest"])
        if type(payload["byte_count"]) is not int:
            raise ContextPackError("Context byte_count must be an integer")  # noqa: TRY003
        pack = cls.build(_decode_items(payload["items"]))
        if payload["digest"] != pack.digest or payload["byte_count"] != pack.byte_count:
            raise ContextPackError("Context packet digest or byte count does not match")  # noqa: TRY003
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """A retained prefix and exact suffix, bound to one base and target digest."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]
    _wire: bytes = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_digest(self.base_digest)
        _validate_digest(self.target_digest)
        if type(self.prefix_items) is not int or self.prefix_items < 0:
            raise ContextPackError("Context prefix_items must be a nonnegative integer")  # noqa: TRY003
        _validate_items(self.appended)
        object.__setattr__(
            self,
            "_wire",
            _json_bytes({
                "schema": _DELTA_SCHEMA,
                "base_digest": self.base_digest,
                "target_digest": self.target_digest,
                "prefix_items": self.prefix_items,
                "appended": _encode_items(self.appended),
            }),
        )

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Retain the longest exact common chunk prefix without retaining sender state."""

        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("Context deltas require complete base and target packs")  # noqa: TRY003
        prefix = 0
        for before, after in zip(base.items, target.items, strict=False):
            if before != after:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @property
    def byte_count(self) -> int:
        """Actual wire bytes; this property is not an extra delta wire field."""

        return len(self._wire)

    def to_bytes(self) -> bytes:
        return self._wire

    @classmethod
    def from_bytes(cls, value: bytes) -> ContextPackDelta:
        payload = _decode_wire(
            value, _DELTA_SCHEMA, {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
        )
        _validate_digest(payload["base_digest"])
        _validate_digest(payload["target_digest"])
        prefix = payload["prefix_items"]
        if type(prefix) is not int or prefix < 0:
            raise ContextPackError("Context prefix_items must be a nonnegative integer")  # noqa: TRY003
        return cls(
            base_digest=str(payload["base_digest"]),
            target_digest=str(payload["target_digest"]),
            prefix_items=prefix,
            appended=_decode_items(payload["appended"]),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Reconstruct from receiver-owned data and validate the target before returning."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("Applying a context delta requires a complete base and delta")  # noqa: TRY003
    if delta.base_digest != base.digest:
        raise ContextPackError("Context delta does not match its base")  # noqa: TRY003
    if delta.prefix_items > len(base.items):
        raise ContextPackError("Context delta prefix exceeds its base")  # noqa: TRY003
    target = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("Reconstructed context does not match the target digest")  # noqa: TRY003
    return target


__all__ = ["ContextPack", "ContextPackDelta", "ContextPackError", "apply_context_pack_delta"]
