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

from powercontext.sources import SourceDefinitionRegistry, SourceMaterialization, TextEvidence
from powercontext.sources.csv_rows import (
    CSV_ROW_SOURCE_DEFINITION,
    CSVDocumentInput,
    CSVRowInput,
    CSVRowSource,
    csv_row_source_id,
    parse_csv_document,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


def test_csv_preserves_order_unicode_whitespace_and_multiline_fields() -> None:
    content = (
        '\ufeff" key ",城市,note,amount,enabled,empty\r\n 001 ,杭州,"first\r\nsecond, ""quoted""",0001,false,\r\n\r\n'
    )
    (row,) = parse_csv_document(
        CSVDocumentInput(document_id="report:2026/10", primary_key=" key ", content=content.encode())
    )
    assert row.columns == (" key ", "城市", "note", "amount", "enabled", "empty")
    assert row.values == (" 001 ", "杭州", 'first\r\nsecond, "quoted"', "0001", "false", "")
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(row))
    assert isinstance(source, CSVRowSource)
    assert source.name == source.source_id == "csv:report%3A2026%2F10:%20001%20"
    assert source.materialization is SourceMaterialization.CAPTURED
    source = CSVRowSource.model_validate_json(source.model_dump_json())
    assert asyncio.run(registry.read(source)) == row
    evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
    expected = dict(zip(row.columns, row.values, strict=True))
    assert evidence.content == json.dumps(expected, ensure_ascii=False, separators=(",", ":"))
    projected_row = evidence.metadata["row"]
    assert isinstance(projected_row, dict)
    assert list(projected_row) == list(row.columns)
    assert evidence.metadata == {
        "document_id": row.document_id,
        "primary_key": row.primary_key,
        "primary_key_value": row.primary_key_value,
        "columns": list(row.columns),
        "row": expected,
    }


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\xef\xbb\xbf",
        b"\nid,name\n1,ok\n",
        b'"",name\n1,ok\n',
        b" \t,name\n1,ok\n",
        b"id,id\n1,ok\n",
        b"id,name\n1,ok\n2\n",
        b"id,name\n1,ok\n2,too,many\n",
        b"id,name\n1,ok\n1,duplicate\n",
        b"id,name\n1,ok\n,empty\n",
        b"id,name\n1,ok\n \t,blank\n",
        b'id,name\n1,ok\n2,"unterminated\n',
        b'id,name\n1,ok\n2,"closed"tail\n',
        b"id,name\n1,ok\n2,\xff\n",
        b'id\n""\n',
        b"id\n \n",
    ],
)
def test_invalid_document_is_rejected_even_after_valid_rows(content: bytes) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id="table", primary_key="id", content=content))


@pytest.mark.parametrize("content", [b"id,name", b"id,name\r\n\r\n\r\n"])
def test_header_only_document_returns_an_empty_tuple(content: bytes) -> None:
    assert parse_csv_document(CSVDocumentInput(document_id="table", primary_key="id", content=content)) == ()


@pytest.mark.parametrize("document_id", ["", " ", " table", "table\n"])
def test_document_identity_is_validated_before_returning_header_only_input(document_id: str) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id=document_id, primary_key="id", content=b"id\n"))


@pytest.mark.parametrize("primary_key", ["", "id", " id", "id "])
def test_primary_key_must_exactly_match_the_original_header(primary_key: str) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id="table", primary_key=primary_key, content=b" id ,name\n"))


def test_row_reordering_and_non_key_edits_preserve_distinct_key_identities() -> None:
    first = parse_csv_document(
        CSVDocumentInput(document_id="table", primary_key="id", content=b"id,name\n key ,old\nkey,second\n")
    )
    second = parse_csv_document(
        CSVDocumentInput(document_id="table", primary_key="id", content=b"id,name\nkey,second\n key ,updated\n")
    )
    assert [csv_row_source_id(row.document_id, row.primary_key_value) for row in first] == [
        "csv:table:%20key%20",
        "csv:table:key",
    ]
    assert {csv_row_source_id(row.document_id, row.primary_key_value) for row in first} == {
        csv_row_source_id(row.document_id, row.primary_key_value) for row in second
    }


def test_identity_encoding_is_collision_free_and_enforces_encoded_length() -> None:
    assert csv_row_source_id("a:b", "c/d%") == "csv:a%3Ab:c%2Fd%25"
    assert csv_row_source_id("a:b", "c") != csv_row_source_id("a", "b:c")
    assert csv_row_source_id("文档", "键") == "csv:%E6%96%87%E6%A1%A3:%E9%94%AE"
    assert len(csv_row_source_id("d", "x" * 250)) == 256
    for key in ("x" * 251, "汉" * 28, "", " \t"):
        with pytest.raises(ValueError):
            csv_row_source_id("d", key)
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id="d", primary_key="id", content=b"id\n" + b"x" * 251))


@pytest.mark.parametrize(
    "changes",
    [
        {"columns": ()},
        {"columns": ("id", "id")},
        {"values": ("1",)},
        {"primary_key_value": "2"},
        {"primary_key": "missing"},
    ],
)
def test_manually_supplied_rows_obey_the_same_record_invariants(changes: dict[str, object]) -> None:
    fields = {
        "document_id": "table",
        "primary_key": "id",
        "primary_key_value": "1",
        "columns": ("id", "name"),
        "values": ("1", "first"),
    }
    with pytest.raises(ValueError):
        CSVRowInput.model_validate(fields | changes)


def test_source_readback_rejects_identity_and_materialization_mismatches() -> None:
    row = parse_csv_document(CSVDocumentInput(document_id="table", primary_key="id", content=b"id\n1"))[0]
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(row))
    for changes in ({"source_id": "csv:table:2"}, {"name": "csv:table:2"}, {"materialization": "referenced"}):
        with pytest.raises(ValueError):
            CSVRowSource.model_validate(source.model_dump() | changes)
