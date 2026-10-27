"""Print one guest's ledger and its revision history from PowerContext.

Reviewers use this to see what the Chatflow actually stored, without opening Dify:
the same ``idempotency_key`` the ``pc_scope`` node uses resolves back to the same
scope, then ``/v1/memory/entries/list`` and ``/v1/memory/changes`` are printed.

Usage (with a PowerContext server on :8000):
    python dify/scripts/memory_audit.py --player shen
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def post(base_url: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(  # noqa: S310 - reviewer-provided loopback URL
        base_url.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        raise SystemExit(f"POST {path} -> HTTP {error.code}\n{body}") from error


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--player", default="shen")
    args = parser.parse_args(argv[1:])

    player = args.player.strip() or "guest"
    scope = post(
        args.base_url,
        "/v1/scopes",
        {
            "title": f"记仇客栈 · {player}",
            "summary": f"客栈掌柜的恩怨账本，玩家 {player} 在此留下的每一笔账。",
            "idempotency_key": f"grudge-inn-{player}",
        },
    )
    scope_id = scope["scope_id"]
    print(f"## scope\n{json.dumps(scope, ensure_ascii=False, indent=2)}")

    entries = post(args.base_url, "/v1/memory/entries/list", {"scope_id": scope_id})
    print(f"## entries ({len(entries.get('entries') or [])})\n{json.dumps(entries, ensure_ascii=False, indent=2)}")

    changes = post(args.base_url, "/v1/memory/changes", {"scope_id": scope_id, "since_revision": 0})
    print(f"## changes\n{json.dumps(changes, ensure_ascii=False, indent=2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
