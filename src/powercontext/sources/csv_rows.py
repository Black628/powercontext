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

"""One ``CAPTURED`` Source per CSV row, addressed by document and primary key."""

from __future__ import annotations

import csv
import io
import json
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

SOURCE_TYPE = "csv-row"
DEFINITION_VERSION = "1"
_BOM = "\ufeff"


class CSVDocumentInput(BaseModel):
    """Offline bytes of one delimited document plus its row key contract."""

    document_id: str
    primary_key: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        _validate_document_id(value)
        return value

    @field_validator("primary_key")
    @classmethod
    def validate_primary_key(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("primary_key must be a non-empty string")  # noqa: TRY003
        return value


class CSVRowInput(BaseModel):
    """One parsed row, keeping the original column order and raw cell values."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_row_width(self) -> CSVRowInput:
        if len(self.columns) != len(self.values):
            raise ValueError("columns and values must have the same width")  # noqa: TRY003
        return self


class CSVRowSource(Source):
    """A captured row Source whose identity is its document and primary key."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> CSVRowSource:
        if self.name != self.source_id:
            raise ValueError("CSV row Source name must equal its source_id")  # noqa: TRY003
        if len(self.columns) != len(self.values):
            raise ValueError("columns and values must have the same width")  # noqa: TRY003
        return self


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the stable, delimiter-safe identity of one row."""

    _validate_document_id(document_id)
    if not isinstance(primary_key_value, str) or not primary_key_value or not primary_key_value.strip():
        raise ValueError("primary key value must be a non-empty string")  # noqa: TRY003
    identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"row Source identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


def parse_csv_document(value: CSVDocumentInput, /) -> tuple[CSVRowInput, ...]:
    """Parse and validate an entire document before returning any row."""

    text = value.content.decode("utf-8")
    if text.startswith(_BOM):
        text = text[1:]
    if not text:
        raise ValueError("CSV document must not be empty")  # noqa: TRY003

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("CSV document must start with a header record") from None  # noqa: TRY003
    except csv.Error as error:
        raise ValueError(f"malformed CSV header: {error}") from error  # noqa: TRY003

    _validate_header(header)
    if value.primary_key not in header:
        raise ValueError(f"primary_key {value.primary_key!r} is not a header column")  # noqa: TRY003
    key_index = header.index(value.primary_key)
    columns = tuple(header)

    rows: list[CSVRowInput] = []
    seen: set[str] = set()
    try:
        for record in reader:
            if not record:
                continue
            if len(record) != len(columns):
                raise ValueError(  # noqa: TRY003
                    f"record width {len(record)} does not match header width {len(columns)}"
                )
            key = record[key_index]
            if not key or not key.strip():
                raise ValueError("primary key value must not be blank")  # noqa: TRY003
            if key in seen:
                raise ValueError(f"duplicate primary key value {key!r}")  # noqa: TRY003
            seen.add(key)
            rows.append(
                CSVRowInput(
                    document_id=value.document_id,
                    primary_key=value.primary_key,
                    primary_key_value=key,
                    columns=columns,
                    values=tuple(record),
                )
            )
    except csv.Error as error:
        raise ValueError(f"malformed CSV record: {error}") from error  # noqa: TRY003

    return tuple(rows)


class _CSVRowAdapter:
    """Resolve parsed rows into Sources and read them back without loss."""

    name = SOURCE_TYPE
    input_class = CSVRowInput
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            definition_version=DEFINITION_VERSION,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class _CSVRowTextEvidenceProjection:
    """Standard ``powercontext.text-evidence`` view of one row."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=SOURCE_TYPE,
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


CSV_ROW_SOURCE_DEFINITION: AdapterSourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition(
    adapter=_CSVRowAdapter(),
    version=DEFINITION_VERSION,
    projections=(_CSVRowTextEvidenceProjection(),),
)


def _validate_document_id(value: str) -> None:
    if not isinstance(value, str) or not value or not value.strip():
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    if value != value.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003


def _validate_header(header: list[str]) -> None:
    if not header:
        raise ValueError("CSV header must not be empty")  # noqa: TRY003
    seen: set[str] = set()
    for name in header:
        if not name or not name.strip():
            raise ValueError("CSV header names must be non-empty and not blank")  # noqa: TRY003
        if name in seen:
            raise ValueError(f"duplicate CSV header name {name!r}")  # noqa: TRY003
        seen.add(name)


__all__: Any = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
