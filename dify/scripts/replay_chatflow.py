"""Replay the Chatflow graph without Dify: real PowerContext calls, canned LLM output.

Why this exists
---------------
The reviewers' Dify instance is where the app really runs, but the two LLM nodes
need a model key. This script walks the exact same graph, in the exact same order,
from the exported DSL file: it resolves ``{{#node.var#}}`` selectors, runs the
``code`` nodes with the same Python, sends the ``http-request`` bodies to a live
PowerContext server and replaces the two ``llm`` nodes with canned output.

It is a wiring check, not a model test: if the graph, the selectors or the JSON
bodies are wrong, this replay fails the same way the app would.

Usage (repository root, with a PowerContext server on :8000):
    python dify/scripts/replay_chatflow.py --player shen --query "我把碗摔了，钱也不赔"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

SELECTOR_RE = re.compile(r"\{\{#([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)#\}\}")
NARRATION = "（阿箬把抹布往柜台上一摔）哟，这碗碎得可真有出息。——说吧，拿什么赔？"
JUDGE_JSON = (
    '{"kind": "offense", "deed": "客人摔碎了柜台上一只青瓷碗，还赖着不赔。", '
    '"quote": "我把碗摔了，钱也不赔，你能拿我怎样？"}'
)


class Replay:
    def __init__(self, dsl: dict[str, Any], base_url: str, judge_text: str = JUDGE_JSON) -> None:
        workflow = dsl["workflow"]
        self.judge_text = judge_text
        self.nodes = {node["id"]: node for node in workflow["graph"]["nodes"]}
        self.edges = workflow["graph"]["edges"]
        self.env = {
            item["name"]: str(item["value"]) for item in workflow["environment_variables"]
        }
        self.base_url = base_url.rstrip("/")
        self.outputs: dict[str, dict[str, str]] = {}
        self.sys = {"query": "", "user_id": "dify-replay-user"}

    # -- selector plumbing -------------------------------------------------
    def resolve(self, selector: list[str]) -> str:
        namespace, key = selector[0], selector[1]
        if namespace == "env":
            return self.env[key]
        if namespace == "sys":
            return self.sys[key]
        value = self.outputs.get(namespace, {}).get(key)
        if value is None:
            raise SystemExit(f"selector {namespace}.{key} has no value yet")
        return value

    def render_text(self, template: str) -> str:
        return SELECTOR_RE.sub(lambda m: self.resolve([m.group(1), m.group(2)]), template)

    def render_json_body(self, template: str) -> Any:
        """Substitute selectors inside a JSON string, then parse it.

        A selector inside a JSON string is escaped; one standing for a whole JSON
        value is inserted verbatim (that is how the ledger entry is passed).
        """
        chunks: list[str] = []
        position = 0
        for match in SELECTOR_RE.finditer(template):
            chunks.append(template[position : match.start()])
            prefix = "".join(chunks)
            value = self.resolve([match.group(1), match.group(2)])
            if prefix.count('"') % 2 == 1:
                chunks.append(json.dumps(value, ensure_ascii=False)[1:-1])
            else:
                try:
                    json.loads(value)
                except ValueError:
                    chunks.append(json.dumps(value, ensure_ascii=False))
                else:
                    chunks.append(value)
            position = match.end()
        chunks.append(template[position:])
        return json.loads("".join(chunks))

    # -- node executors ----------------------------------------------------
    def run_code(self, node: dict[str, Any]) -> dict[str, str]:
        data = node["data"]
        namespace: dict[str, Any] = {}
        exec(data["code"], namespace)  # noqa: S102 - the DSL is our own file
        kwargs = {
            item["variable"]: self.resolve(item["value_selector"]) for item in data["variables"]
        }
        produced = namespace["main"](**kwargs)
        return {key: str(produced.get(key, "")) for key in data["outputs"]}

    def run_http(self, node: dict[str, Any]) -> dict[str, str]:
        data = node["data"]
        url = self.render_text(data["url"])
        payload = self.render_json_body(data["body"]["data"][0]["value"])
        request = urllib.request.Request(  # noqa: S310 - reviewer-provided loopback URL
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method=data["method"].upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                body = response.read().decode("utf-8")
                status_code = response.status
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")
            raise SystemExit(f"{node['id']} -> HTTP {error.code}\n{body}") from error
        return {"body": body, "status_code": str(status_code)}

    def run_start(self, node: dict[str, Any]) -> dict[str, str]:
        return {
            item["variable"]: str(self.sys.get(item["variable"], ""))
            for item in node["data"]["variables"]
        }

    def run_llm(self, node: dict[str, Any]) -> dict[str, str]:
        return {"text": self.judge_text if node["id"] == "judge" else NARRATION}

    def run_answer(self, node: dict[str, Any]) -> None:
        text = self.render_text(node["data"]["answer"])
        print("\n### answer\n" + text)
        if "恩怨账本" not in text:
            raise SystemExit("answer is missing the ledger panel")

    # -- walker ------------------------------------------------------------
    def start(self) -> tuple[str, str]:
        current = next(node_id for node_id, node in self.nodes.items() if node["data"]["type"] == "start")
        while True:
            node = self.nodes[current]
            kind = node["data"]["type"]
            print(f"\n### {current} ({kind})")
            if kind == "start":
                produced = self.run_start(node)
            elif kind == "code":
                produced = self.run_code(node)
            elif kind == "http-request":
                produced = self.run_http(node)
            elif kind == "llm":
                produced = self.run_llm(node)
            elif kind == "answer":
                self.run_answer(node)
                return current, "done"
            else:
                raise SystemExit(f"replay does not support node type {kind} ({current})")
            self.outputs[current] = produced
            for key, value in produced.items():
                print(f"  {key} = {value[:160]}")
            outgoing = [edge for edge in self.edges if edge["source"] == current]
            if not outgoing:
                raise SystemExit(f"{current} has no outgoing edge")
            current = outgoing[0]["target"]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsl", default="dify/dsl/grudge-inn.chatflow.yml")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--player", default="shen")
    parser.add_argument("--user-id", default="dify-replay-user", help="stand-in for sys.user_id when --player is empty")
    parser.add_argument("--query", default="我把碗摔了，钱也不赔，你能拿我怎样？")
    parser.add_argument("--judge", default=JUDGE_JSON, help="canned judge JSON: what this turn should be recorded as")
    parser.add_argument("--judge-file", help="read the canned judge JSON from a file instead (avoids shell quoting)")
    args = parser.parse_args(argv[1:])

    judge_text = Path(args.judge_file).read_text(encoding="utf-8") if args.judge_file else args.judge
    dsl = yaml.safe_load(Path(args.dsl).read_text(encoding="utf-8"))
    replay = Replay(dsl, args.base_url, judge_text=judge_text)
    replay.sys = {"query": args.query, "user_id": args.user_id}
    replay.sys["player_id"] = args.player
    replay.start()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
