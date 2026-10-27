"""Configuration for the SDD context module.

All paths are resolved against the submission root (``minimax/``) so the module
works the same from a clean checkout and on a judge machine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_POWERCONTEXT_URL = "http://127.0.0.1:8000"
DEFAULT_SCOPE_TITLE = "minimax-sdd-pricing"


@dataclass(frozen=True)
class Config:
    """Runtime configuration for one SDD invocation."""

    root: Path
    workspace: Path
    data_dir: Path
    db_target: Path
    backend: str
    powercontext_url: str
    powercontext_token: str | None
    scope_title: str

    @classmethod
    def load(
        cls,
        *,
        workspace: str | None = None,
        db: str | None = None,
        backend: str | None = None,
        powercontext_url: str | None = None,
    ) -> "Config":
        root = ROOT
        ws = Path(workspace or os.environ.get("SDD_WORKSPACE") or root / "workspace").resolve()
        data_dir = Path(os.environ.get("SDD_DATA_DIR") or root / ".data").resolve()
        db_target = Path(db or os.environ.get("SDD_DB_PATH") or data_dir / "index").resolve()
        return cls(
            root=root,
            workspace=ws,
            data_dir=data_dir,
            db_target=db_target,
            backend=(backend or os.environ.get("SDD_BACKEND") or "auto").lower(),
            powercontext_url=(
                powercontext_url or os.environ.get("POWERCONTEXT_URL") or DEFAULT_POWERCONTEXT_URL
            ).rstrip("/"),
            powercontext_token=os.environ.get("POWERCONTEXT_TOKEN") or None,
            scope_title=os.environ.get("SDD_SCOPE_TITLE") or DEFAULT_SCOPE_TITLE,
        )
