"""运行配置：全部从环境变量读取，带可用默认值；不硬编码任何机器路径或凭据。"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .util import DEFAULT_HOME, env, env_bool, read_json, write_json

#: seekdb 端口探测顺序（不同部署实例端口不同，例如 2881 或 2882）
DEFAULT_PORT_CANDIDATES = [2881, 2882, 2880, 2883, 2884, 3306]


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass
class Config:
    # --- seekdb（工程资料库）---
    db_host: str = "127.0.0.1"
    db_port: int = 2882
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "sdd_minimax"
    # --- 工程 / 记录 ---
    home: Path = field(default_factory=lambda: DEFAULT_HOME)
    project_root: Path = field(default_factory=lambda: DEFAULT_HOME / "demo_project")
    records_dir: Path = field(default_factory=lambda: DEFAULT_HOME / "records")
    sample_dir: Path = field(default_factory=lambda: DEFAULT_HOME / "sample")
    state_dir: Path = field(default_factory=lambda: DEFAULT_HOME / ".state")
    # --- MiniMax（OpenAI 兼容）---
    minimax_api_key: Optional[str] = None
    minimax_base_url: str = "https://api.minimaxi.com/v1"
    minimax_model: str = "MiniMax-M2"
    minimax_fallbacks: List[str] = field(default_factory=lambda: ["MiniMax-M3.1-Flash", "MiniMax-M2.7"])
    minimax_timeout: int = 180
    # --- PowerContext（状态复用）---
    pc_base_url: str = "http://127.0.0.1:8001"
    pc_scope_id: Optional[str] = None
    pc_token: Optional[str] = None
    pc_backend: str = "auto"          # auto | powercontext | file
    # --- seekdb 是否已探测 ---
    port_detected: bool = False

    # ---------------- 派生路径 ----------------
    @property
    def spec_dir(self) -> Path:
        return self.home / "sample"
    @property
    def test_log_dir(self) -> Path:
        return self.records_dir / "test-logs"

    @property
    def minimax_log(self) -> Path:
        return self.records_dir / "minimax.jsonl"

    @property
    def seekdb_log(self) -> Path:
        return self.records_dir / "seekdb-queries.md"

    @property
    def pc_log(self) -> Path:
        return self.records_dir / "powercontext.md"

    # ---------------- 探测 ----------------
    def resolve_port(self) -> int:
        """确认 seekdb 实际监听端口；结果缓存到 .state/db-port.json。"""
        cache = self.state_dir / "db-port.json"
        candidates: List[int] = []
        if env("SEEKDB_PORT"):
            candidates.append(int(env("SEEKDB_PORT")))
        cached = read_json(cache, {}) or {}
        if cached.get("port") and cached.get("host") == self.db_host:
            candidates.append(int(cached["port"]))
        candidates.extend(DEFAULT_PORT_CANDIDATES)
        seen, ordered = set(), []
        for port in candidates:
            if port not in seen:
                seen.add(port)
                ordered.append(port)
        for port in ordered:
            if _port_open(self.db_host, port):
                self.db_port = port
                self.port_detected = True
                write_json(cache, {"host": self.db_host, "port": port})
                return port
        return self.db_port

    def resolve_pc_base_url(self) -> str:
        """确认 PowerContext Server 地址（默认 8001，其次 8000）。"""
        if env("POWERCONTEXT_BASE_URL"):
            return self.pc_base_url
        for port in (8001, 8000):
            url = f"http://127.0.0.1:{port}"
            if _port_open("127.0.0.1", port):
                self.pc_base_url = url
                return url
        return self.pc_base_url


def load_config(argv_project: Optional[str] = None, argv_records: Optional[str] = None) -> Config:
    home = Path(env("SDD_HOME", str(DEFAULT_HOME))).expanduser()
    cfg = Config(
        db_host=env("SEEKDB_HOST", "127.0.0.1"),
        db_port=int(env("SEEKDB_PORT", "0") or 0) or DEFAULT_PORT_CANDIDATES[1],
        db_user=env("SEEKDB_USER", "root"),
        db_password=env("SEEKDB_PASSWORD", ""),
        db_name=env("SDD_DB_NAME", "sdd_minimax"),
        home=home,
        project_root=Path(env("SDD_PROJECT", str(home / "demo_project"))).expanduser(),
        records_dir=Path(env("SDD_RECORDS_DIR", str(home / "records"))).expanduser(),
        sample_dir=Path(env("SDD_SAMPLE_DIR", str(home / "sample"))).expanduser(),
        state_dir=Path(env("SDD_STATE_DIR", str(home / ".state"))).expanduser(),
        minimax_api_key=env("MINIMAX_API_KEY"),
        minimax_base_url=env("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/"),
        minimax_model=env("MINIMAX_MODEL", "MiniMax-M2"),
        minimax_fallbacks=[m.strip() for m in
                           env("MINIMAX_MODEL_FALLBACKS", "MiniMax-M3.1-Flash,MiniMax-M2.7").split(",")
                           if m.strip()],
        minimax_timeout=int(env("MINIMAX_TIMEOUT", "180")),
        pc_base_url=env("POWERCONTEXT_BASE_URL", "http://127.0.0.1:8001").rstrip("/"),
        pc_scope_id=env("POWERCONTEXT_SCOPE_ID"),
        pc_token=env("POWERCONTEXT_SERVER_AUTH_TOKEN"),
        pc_backend=env("SDD_STATE_BACKEND", "auto"),
    )
    if argv_project:
        cfg.project_root = Path(argv_project).expanduser()
    if argv_records:
        cfg.records_dir = Path(argv_records).expanduser()
    cfg.project_root = cfg.project_root.resolve()
    cfg.records_dir = cfg.records_dir.resolve()
    cfg.sample_dir = cfg.sample_dir.resolve()
    cfg.state_dir = cfg.state_dir.resolve()
    cfg.resolve_pc_base_url()
    return cfg


def db_summary(cfg: Config) -> dict:
    return {
        "host": cfg.db_host,
        "port": cfg.db_port,
        "database": cfg.db_name,
        "user": cfg.db_user,
        "table": "sdd_docs",
    }


def minimax_credentials_present(cfg: Config) -> bool:
    return bool(cfg.minimax_api_key)


def os_name() -> str:
    return os.name
