"""Start PowerContext from whichever install this checkout has.

Two supported setups:

* the package is installed (``pip install "powercontext[server]==1.2.0"``), or
* this is a full PowerContext checkout with ``src/`` (no editable install needed).

It points runtime data at ``dify/.data`` so the demo stays inside the deliverable
directory, and it binds loopback with the default (no-auth) access mode.

Usage (from the repository root):
    python dify/scripts/run_powercontext.py                # Windows: .venv/Scripts/python.exe
    python dify/scripts/run_powercontext.py --port 8000
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
DATA = REPO_ROOT / "dify" / ".data"


def load_app():
    try:
        from powercontext.server.factory import create_server_app  # noqa: PLC0415
        from powercontext.server.settings import ServerSettings  # noqa: PLC0415

        return create_server_app, ServerSettings
    except ModuleNotFoundError:
        if not SRC.is_dir():
            raise SystemExit(
                "PowerContext is not importable. Install it with:\n"
                '    pip install "powercontext[server]==1.2.0"\n'
                "or run this script from a full PowerContext checkout."
            ) from None
        sys.path.insert(0, str(SRC))
        from powercontext.server.factory import create_server_app  # noqa: PLC0415
        from powercontext.server.settings import ServerSettings  # noqa: PLC0415

        return create_server_app, ServerSettings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv[1:])

    os.environ.setdefault("POWERCONTEXT_HOME", str(DATA))
    create_server_app, server_settings = load_app()

    import uvicorn  # noqa: PLC0415

    application = create_server_app(settings=server_settings())
    uvicorn.run(application, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
