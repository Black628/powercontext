"""Start the PowerContext CLI with a SQLite library that satisfies its checks.

PowerContext 1.4.1 refuses to start when Python's bundled ``sqlite3`` is older
than 3.38 (for example the Python 3.12 shipped by Ubuntu 22.04 uses system
SQLite 3.37.2).  ``requirements.txt`` therefore installs ``pysqlite3-binary``
on Linux/x86_64; this launcher swaps it in before PowerContext imports
``sqlite3`` and then runs the regular CLI.

Usage: ``python scripts/powercontext_launcher.py server run --no-env-file``.
"""

import sys


def _use_modern_sqlite() -> None:
    import sqlite3

    current = tuple(int(part) for part in sqlite3.sqlite_version.split(".")[:2])
    if current >= (3, 38):
        return
    try:
        import pysqlite3
    except ImportError as error:  # pragma: no cover - depends on the host
        raise SystemExit(
            "PowerContext requires SQLite 3.38 or newer, but this Python uses "
            f"{sqlite3.sqlite_version} and pysqlite3-binary is not installed.\n"
            "Install it with `pip install pysqlite3-binary` (Linux x86_64), or use a "
            "Python distribution whose sqlite3 module is new enough (for example a "
            "conda-forge Python 3.12)."
        ) from error
    sys.modules["sqlite3"] = pysqlite3
    dbapi2 = getattr(pysqlite3, "dbapi2", None)
    if dbapi2 is not None:
        sys.modules["sqlite3.dbapi2"] = dbapi2


def main() -> int:
    _use_modern_sqlite()
    from powercontext.cli.app import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
