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

"""Backend selection.

``COACH_BACKEND`` accepts ``auto`` (default), ``seekdb`` or ``mirror``.
``auto`` prefers seekdb when the runtime is configured and importable, and
otherwise falls back to the SQLite mirror so the demo still starts. The
fallback reason is surfaced to the UI and the evidence panel.
"""

from __future__ import annotations

import logging

from ..config import Settings
from ..embedding import Embedder
from .base import EvidenceLog, MaterialStore, SearchHit, TraceEntry, fuse_rrf
from .mirror import MirrorStore
from .seekdb import SeekdbStore

LOGGER = logging.getLogger(__name__)

__all__ = [
    "EvidenceLog",
    "MaterialStore",
    "MirrorStore",
    "SearchHit",
    "SeekdbStore",
    "TraceEntry",
    "build_store",
    "fuse_rrf",
]


def build_store(settings: Settings, embedder: Embedder) -> tuple[MaterialStore, str]:
    """Return the store plus a human-readable note about the chosen backend."""
    preference = settings.backend_preference
    if preference == "mirror":
        store = MirrorStore(settings, embedder)
        store.open()
        return store, "COACH_BACKEND=mirror"
    if preference == "seekdb" or settings.seekdb_configured:
        try:
            store = SeekdbStore(settings, embedder)
            store.open()
            LOGGER.info("seekdb backend ready (%s)", store.describe().get("target"))
            return store, ""
        except Exception as error:  # noqa: BLE001 - degrade with a visible reason
            if preference == "seekdb":
                raise
            note = f"seekdb unavailable ({type(error).__name__}: {str(error)[:180]}); fell back to SQLite mirror"
            LOGGER.warning(note)
            fallback = MirrorStore(settings, embedder)
            fallback.open()
            return fallback, note
    store = MirrorStore(settings, embedder)
    store.open()
    return store, "SEEKDB_PATH/SEEKDB_URI not set"
