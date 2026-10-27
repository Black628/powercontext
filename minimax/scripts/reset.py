#!/usr/bin/env python3
"""Restore the demo project to its *before* state.

Copies ``evidence/before/engine.py`` over ``workspace/src/pricing/engine.py``
and (unless ``--keep-data``) removes the local material index and runtime data:

    .data/          material index + SQL log + PowerContext request log
    run/            PowerContext SQLite database

The evidence snapshots (before/after, logs, diff) are never removed.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-data", action="store_true", help="keep .data/ and run/")
    args = parser.parse_args()

    shutil.copyfile(ROOT / "evidence" / "before" / "engine.py", ROOT / "workspace" / "src" / "pricing" / "engine.py")
    print("restored workspace/src/pricing/engine.py from evidence/before/engine.py")
    if not args.keep_data:
        for directory in (".data", "run"):
            path = ROOT / directory
            if path.exists():
                shutil.rmtree(path)
                print(f"removed {directory}/")
    print("reset done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
