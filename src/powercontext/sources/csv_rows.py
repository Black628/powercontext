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

"""Offline CSV records captured as independently addressable Source evidence."""

from __future__ import annotations

import csv
import io
import json
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class CSVDocumentInput(BaseModel):
    """Complete UTF-8 CSV bytes with an exact primary-key column name."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One complete record whose original column order and strings are retained."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_record(self) -> CSVRowInput:
        csv_row_source_id(self.document_id, self.primary_key_value)
        _validate_columns(self.columns, self.primary_key)
        if len(self.values) != len(self.columns):
            raise ValueError("CSV record width must match its header")  # noqa: TRY003
        if self.values[self.columns.index(self.primary_key)] != self.primary_key_value:
            raise ValueError("CSV primary key value must match its record field")  # noqa: TRY003
        return self


class CSVRowSource(Source, CSVRowInput):
    """Captured CSV record, readable without reopening the original document."""

    source_id: str
    definition_version: Literal["1"] = "1"
    materialization: Literal[SourceMaterialization.CAPTURED] = SourceMaterialization.CAPTURED

    @model_validator(mode="after")
    def validate_identity(self) -> CSVRowSource:
        identity = csv_row_source_id(self.document_id, self.primary_key_value)
        if self.source_id != identity or self.name != identity:
            raise ValueError("CSV Source name and source_id must match its record identity")  # noqa: TRY003
        return self


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Encode document and key separately, preserving significant key whitespace."""

    if not document_id or document_id != document_id.strip():
        raise ValueError("CSV document_id must be non-empty without surrounding whitespace")  # noqa: TRY003
    if not primary_key_value.strip():
        raise ValueError("CSV primary key value must not be empty or whitespace")  # noqa: TRY003
    identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"CSV Source identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


def _validate_columns(columns: tuple[str, ...], primary_key: str) -> None:
    if not columns or any(not column.strip() for column in columns):
        raise ValueError("CSV header columns must not be empty or whitespace")  # noqa: TRY003
    if len(set(columns)) != len(columns):
        raise ValueError("CSV header columns must be unique")  # noqa: TRY003
    if not primary_key or primary_key not in columns:
        raise ValueError("CSV primary_key must exactly match a header column")  # noqa: TRY003


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Validate the complete document before returning any captured row inputs.

    Empty physical records after the header are ignored. Quoted empty fields
    remain records and are validated normally. ``newline=""`` preserves line
    endings inside quoted fields instead of translating them.
    """

    if not value.document_id or value.document_id != value.document_id.strip():
        raise ValueError("CSV document_id must be non-empty without surrounding whitespace")  # noqa: TRY003
    reader = csv.reader(io.StringIO(value.content.decode("utf-8-sig"), newline=""), strict=True)
    try:
        columns = tuple(next(reader, ()))
        _validate_columns(columns, value.primary_key)
        key_index = columns.index(value.primary_key)
        keys: set[str] = set()
        rows: list[CSVRowInput] = []
        for values in reader:
            if not values:
                continue
            if len(values) != len(columns):
                raise ValueError("CSV record width must match its header")  # noqa: TRY003
            key = values[key_index]
            if key in keys:
                raise ValueError("CSV primary key values must be unique within a document")  # noqa: TRY003
            rows.append(
                CSVRowInput(
                    document_id=value.document_id,
                    primary_key=value.primary_key,
                    primary_key_value=key,
                    columns=columns,
                    values=tuple(values),
                )
            )
            keys.add(key)
    except csv.Error as error:
        raise ValueError("Invalid CSV document") from error  # noqa: TRY003
    return tuple(rows)


class _CSVRowAdapter:
    input_class = CSVRowInput
    name = "csv-row"
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(name=identity, source_id=identity, **value.model_dump())

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput.model_validate(source.model_dump())


class _CSVRowTextEvidence:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type="csv-row",
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition(
    _CSVRowAdapter(), projections=(_CSVRowTextEvidence(),)
)

__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
