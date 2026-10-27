"""Retire every active ledger entry for one player.

Use it before re-running the verification sample, so the "first visit" state is
really empty. Each retire moves the Memory head, so the citation must be read
again after every call (a stale citation is rejected with ``revision_conflict``).

Usage:
    python dify/scripts/reset_player.py --player shen
"""

from __future__ import annotations

import argparse
import json
import sys

from pc_http_demo import Client, ensure_scope


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--player", default="shen")
    args = parser.parse_args(argv[1:])

    client = Client(args.base_url)
    scope = ensure_scope(client, args.player)
    scope_id = scope["scope_id"]
    retired = 0
    while True:
        entries = client.post("/v1/memory/entries/list", {"scope_id": scope_id})["entries"]
        active = [entry for entry in entries if entry["state"] == "active"]
        if not active:
            break
        client.post(
            "/v1/memory/entries/retire",
            {"scope_id": scope_id, "citation": active[0]["citation"], "reason": "reset for verification"},
        )
        retired += 1
    print(json.dumps({"player": args.player, "scope_id": scope_id, "retired": retired}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
