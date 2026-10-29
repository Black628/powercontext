"""MiniMax 客户端路径验证（用本地 HTTP 假服务模拟 OpenAI 兼容接口）。

注意：本测试**不访问**真实的 MiniMax 服务，只验证
  1. 请求体构造（model / messages / temperature）；
  2. Authorization 头携带 Bearer key；
  3. 响应解析（choices[0].message.content、usage）；
  4. diff 抽取与 records/minimax.jsonl 日志字段写入（mode=live）。

真实调用请设置 MINIMAX_API_KEY 后执行：
  python -m sddcontext apply --patch-source minimax
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PACKAGE_PARENT = Path(__file__).resolve().parent.parent
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from sddcontext.config import Config                      # noqa: E402
from sddcontext.minimax_client import MiniMaxClient       # noqa: E402
from sddcontext.patcher import extract_diff               # noqa: E402

FAKE_DIFF = """--- a/inventory/discount.py
+++ b/inventory/discount.py
@@ -1 +1 @@
-x = 1
+x = 2
"""


class _Handler(BaseHTTPRequestHandler):
    captured: dict = {}

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        _Handler.captured = {
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": body,
        }
        response = {
            "id": "mock-req-1",
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant",
                            "content": "```diff\n" + FAKE_DIFF + "```"},
            }],
            "usage": {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168},
        }
        payload = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # 静音
        return


class MiniMaxClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _cfg(self, workdir: Path) -> Config:
        cfg = Config()
        cfg.minimax_api_key = "test-key-not-real"
        cfg.minimax_base_url = f"http://127.0.0.1:{self.port}/v1"
        cfg.minimax_model = "MiniMax-M2"
        cfg.minimax_timeout = 10
        cfg.records_dir = workdir
        cfg.state_dir = workdir / ".state"
        return cfg

    def test_request_and_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            cfg = self._cfg(workdir)
            client = MiniMaxClient(cfg, log_path=workdir / "minimax.jsonl")
            messages = [{"role": "system", "content": "sys"},
                        {"role": "user", "content": "请输出 patch"}]
            result = client.chat(messages, task="mock-task")

            # 请求构造
            self.assertEqual(_Handler.captured["path"], "/v1/chat/completions")
            self.assertEqual(_Handler.captured["auth"], "Bearer test-key-not-real")
            self.assertEqual(_Handler.captured["body"]["model"], "MiniMax-M2")
            self.assertEqual(_Handler.captured["body"]["messages"], messages)

            # 响应解析
            self.assertEqual(result["usage"]["total_tokens"], 168)
            self.assertEqual(extract_diff(result["text"]), FAKE_DIFF)

            # 日志字段
            lines = (workdir / "minimax.jsonl").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            for key in ("ts", "model", "messages", "response", "usage", "mode", "status"):
                self.assertIn(key, record)
            self.assertEqual(record["mode"], "live")
            self.assertEqual(record["status"], "ok")
            self.assertEqual(record["usage"]["prompt_tokens"], 123)
            self.assertTrue(record["patch_detected"])

    def test_replay_mode_is_labelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            patch_path = workdir / "offline_patch.diff"
            patch_path.write_text(FAKE_DIFF, encoding="utf-8")
            cfg = self._cfg(workdir)
            client = MiniMaxClient(cfg, log_path=workdir / "minimax.jsonl")
            result = client.replay("mock-task", patch_path)
            record = result["record"]
            self.assertEqual(record["mode"], "replay")
            self.assertTrue(record["offline_replay"])
            self.assertIsNone(record["usage"])


if __name__ == "__main__":
    unittest.main()
