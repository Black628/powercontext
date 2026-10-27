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

"""Runtime configuration for the Recall Coach demo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    return int(raw)


def _env_path(name: str, default: Path | None = None) -> Path | None:
    raw = _env(name)
    if not raw:
        return default
    return Path(raw).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    """All knobs this demo understands. Every value has an offline default."""

    data_dir: Path
    embedding_dim: int
    embedding_kind: str
    embedding_api_base: str
    embedding_api_key: str
    embedding_model: str
    seekdb_path: Path | None
    seekdb_uri: str
    seekdb_user: str
    seekdb_password: str
    seekdb_db: str
    powercontext_mode: str
    powercontext_url: str
    powercontext_workspace: Path
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    learner_name: str
    backend_preference: str
    http_host: str
    http_port: int

    @property
    def embedding_configured(self) -> bool:
        return bool(self.embedding_api_key and self.embedding_model)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_model)

    @property
    def seekdb_configured(self) -> bool:
        return bool(self.seekdb_path or self.seekdb_uri)

    def describe(self) -> dict[str, object]:
        """Public, secret-free view used by the UI and the evidence page."""
        return {
            "data_dir": str(self.data_dir),
            "embedding": {
                "kind": self.embedding_kind,
                "model": self.embedding_model or "builtin-hash",
                "dim": self.embedding_dim,
            },
            "seekdb": {
                "configured": self.seekdb_configured,
                "path": str(self.seekdb_path) if self.seekdb_path else None,
                "uri": self.seekdb_uri or None,
                "db": self.seekdb_db,
            },
            "powercontext": {
                "mode": self.powercontext_mode,
                "url": self.powercontext_url or "embedded",
                "workspace": str(self.powercontext_workspace),
            },
            "llm": {"model": self.llm_model or None, "configured": self.llm_configured},
            "learner": self.learner_name,
            "backend_preference": self.backend_preference,
        }


def load_settings() -> Settings:
    data_dir = _env_path("COACH_DATA_DIR", DEFAULT_DATA_DIR)
    assert data_dir is not None
    workspace = _env_path("COACH_PC_WORKSPACE", data_dir / "powercontext")
    assert workspace is not None
    embedding_key = _env("COACH_EMBEDDING_API_KEY") or _env("OPENAI_API_KEY")
    llm_key = _env("COACH_LLM_API_KEY") or _env("OPENAI_API_KEY")
    return Settings(
        data_dir=data_dir,
        embedding_dim=_env_int("COACH_EMBEDDING_DIM", 1024),
        embedding_kind="openai" if (embedding_key and _env("COACH_EMBEDDING_MODEL")) else "hash",
        embedding_api_base=_env("COACH_EMBEDDING_BASE_URL") or _env("OPENAI_BASE_URL"),
        embedding_api_key=embedding_key,
        embedding_model=_env("COACH_EMBEDDING_MODEL"),
        seekdb_path=_env_path("SEEKDB_PATH"),
        seekdb_uri=_env("SEEKDB_URI"),
        seekdb_user=_env("SEEKDB_USER", "root"),
        seekdb_password=_env("SEEKDB_PASSWORD", ""),
        seekdb_db=_env("SEEKDB_DB", "recall_coach"),
        powercontext_mode=_env("COACH_PC_MODE", "embedded") or "embedded",
        powercontext_url=_env("COACH_PC_URL"),
        powercontext_workspace=workspace,
        llm_base_url=_env("COACH_LLM_BASE_URL") or _env("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        llm_api_key=llm_key,
        llm_model=_env("COACH_LLM_MODEL"),
        learner_name=_env("COACH_LEARNER", "小林"),
        backend_preference=_env("COACH_BACKEND", "auto") or "auto",
        http_host=_env("COACH_HOST", "127.0.0.1") or "127.0.0.1",
        http_port=_env_int("COACH_PORT", 8848),
    )
