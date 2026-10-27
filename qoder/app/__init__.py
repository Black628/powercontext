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

"""知新 Recall Coach — a study coach that remembers the learner.

Package layout:

* :mod:`app.config` — environment-driven settings, no secrets required
* :mod:`app.text` — tokenisation, keywords, passage splitting
* :mod:`app.embedding` — hashed or OpenAI-compatible embeddings
* :mod:`app.store` — seekdb backend plus the SQLite mirror fallback
* :mod:`app.pc_memory` — PowerContext client and embedded Server bootstrap
* :mod:`app.coach` — the study loop that wires retrieval to memory
* :mod:`app.main` — FastAPI surface and the static web UI
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
