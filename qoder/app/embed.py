"""embedding 抽象层。

- 默认 provider 为 hash：确定性哈希向量，完全离线、无需任何 API Key；
- 可选 sentence_transformers：本地加载模型（需自行安装依赖）；
- 严禁在代码里写死任何密钥，模型名/维度全部来自环境变量（占位值即可）。
"""

import hashlib
import json
import math
import os
import re

from dotenv import load_dotenv

# 允许从当前工作目录或 app/ 目录旁的 .env 读取配置
load_dotenv()

# 向量维度：与 scripts/init_seekdb.sql 的 VECTOR(384) 保持一致
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "hash").strip().lower()
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")  # 占位：如 sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2

# 中文单字/双字 + 拉丁词的粗分词，用于哈希词袋
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def _tokens(text: str) -> list:
    """把文本切成 token：英文按词，中文按单字并追加相邻双字组合。"""
    raw = [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]
    out = list(raw)
    out.extend(raw[i] + raw[i + 1] for i in range(len(raw) - 1))
    return out


class HashEmbedder:
    """确定性哈希向量（feature hashing），离线可用、同输入必同输出。"""

    dim = EMBEDDING_DIM
    name = "hash"

    def embed(self, texts: list) -> list:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list:
        vec = [0.0] * self.dim
        for tok in _tokens(text):
            h = int.from_bytes(
                hashlib.md5(tok.encode("utf-8")).digest()[:8], "big"
            )
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 == 0 else -1.0
            vec[idx] += sign
        # L2 归一化，便于 COSINE_DISTANCE 计算
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class SentenceTransformerEmbedder:
    """本地 sentence-transformers 模型；仅在显式配置时启用。"""

    def __init__(self):
        if not EMBEDDING_MODEL:
            raise RuntimeError("EMBEDDING_PROVIDER=sentence_transformers 时必须设置 EMBEDDING_MODEL")
        # 延迟导入：默认 hash 路径不引入重依赖
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(EMBEDDING_MODEL)
        self.dim = self._model.get_sentence_embedding_dimension()
        self.name = "sentence_transformers"

    def embed(self, texts: list) -> list:
        return [list(map(float, v)) for v in self._model.encode(texts)]


_embedder = None


def get_embedder():
    """按环境变量返回进程内单例 embedder。"""
    global _embedder
    if _embedder is None:
        if EMBEDDING_PROVIDER == "sentence_transformers":
            _embedder = SentenceTransformerEmbedder()
        else:
            _embedder = HashEmbedder()
    return _embedder


def embed_texts(texts: list) -> list:
    """批量向量化。"""
    return get_embedder().embed(texts)


def embed_one(text: str) -> list:
    """单条向量化。"""
    return embed_texts([text])[0]


def vector_to_sql(vec: list) -> str:
    """把向量序列化为 seekdb VECTOR 字面量，如 '[0.1, 0.2, ...]'。"""
    return json.dumps(vec)
