"""
Embedding 供應者。只有兩個：Voyage（正式）與 Fake（測試／沒 key 時試跑）。

Voyage 直接打 REST，不裝官方 SDK（見 requirements.txt 的說明）。
API：POST https://api.voyageai.com/v1/embeddings
     {"input": [...], "model": "...", "input_type": "document" | "query"}
     → {"data": [{"embedding": [...], "index": 0}, ...], "usage": {"total_tokens": n}}

input_type 是 Voyage 的非對稱檢索：文件端用 document、查詢端用 query，
兩邊各自加了不同的前綴再編碼，同一套向量空間但召回比對稱的好。
存進 DB 的一律是 document 向量。

★ 回答用哪家 LLM 跟這裡無關。向量只能由同一個模型產生才能比對，
   使用者在前端切換 Claude / OpenAI / Gemini 不會動到這一層。★
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
import time
from typing import Literal, Protocol

InputType = Literal["document", "query"]


class Embedder(Protocol):
    name: str            # 寫進 kb_chunk.embed_model；換模型 → 名字不同 → 全部重算
    dim: int
    calls: int           # 統計用：這次 ingest 打了幾次 API
    texts: int
    tokens: int

    def embed(self, texts: list[str], input_type: InputType = "document") -> list[list[float]]: ...


class FakeEmbedder:
    """
    向量 = 文字 sha256 展開成 dim 個數字後正規化。只保證「同文同向量」。
    相似度沒有語意，但整條 ingest → 寫入 → 檢索的路都走得通，測試靠它。
    """

    def __init__(self, dim: int | None = None):
        self.dim = dim or int(os.getenv("EMBEDDING_DIM", "1024"))
        self.name = f"fake-{self.dim}"
        self.calls = self.texts = self.tokens = 0

    def embed(self, texts: list[str], input_type: InputType = "document") -> list[list[float]]:
        self.calls += 1
        self.texts += len(texts)
        out = []
        for t in texts:
            seed = hashlib.sha256(t.encode("utf-8")).digest()
            vals: list[float] = []
            counter = 0
            while len(vals) < self.dim:
                block = hashlib.sha256(seed + struct.pack(">I", counter)).digest()
                vals.extend((b - 128) / 128.0 for b in block)
                counter += 1
            vals = vals[:self.dim]
            norm = math.sqrt(sum(v * v for v in vals)) or 1.0
            out.append([v / norm for v in vals])
        return out


class VoyageEmbedder:
    URL = "https://api.voyageai.com/v1/embeddings"
    BATCH = 64                     # Voyage 單次上限更高，但小批次失敗重試成本低

    def __init__(self, api_key: str | None = None, model: str | None = None, dim: int | None = None):
        self.api_key = api_key or os.getenv("VOYAGE_API_KEY")
        if not self.api_key:
            raise RuntimeError("EMBEDDING_PROVIDER=voyage 但 VOYAGE_API_KEY 沒設。"
                               "本機試跑可以先用 EMBEDDING_PROVIDER=fake。")
        self.model = model or os.getenv("VOYAGE_MODEL", "voyage-3.5")
        self.dim = dim or int(os.getenv("EMBEDDING_DIM", "1024"))
        self.name = self.model
        self.calls = self.texts = self.tokens = 0

    def embed(self, texts: list[str], input_type: InputType = "document") -> list[list[float]]:
        import requests

        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            batch = texts[i:i + self.BATCH]
            payload = {"input": batch, "model": self.model, "input_type": input_type,
                       "output_dimension": self.dim}
            data = self._post(requests, payload)
            rows = sorted(data["data"], key=lambda r: r["index"])
            if len(rows) != len(batch):
                raise RuntimeError(f"Voyage 回了 {len(rows)} 個向量但送了 {len(batch)} 段")
            for r in rows:
                if len(r["embedding"]) != self.dim:
                    raise RuntimeError(f"Voyage 回的向量是 {len(r['embedding'])} 維，EMBEDDING_DIM 是 {self.dim}")
            out.extend(r["embedding"] for r in rows)
            self.calls += 1
            self.texts += len(batch)
            self.tokens += int(data.get("usage", {}).get("total_tokens", 0))
        return out

    def _post(self, requests, payload: dict) -> dict:
        delay = 2.0
        for attempt in range(6):
            resp = requests.post(self.URL, json=payload, timeout=60,
                                 headers={"Authorization": f"Bearer {self.api_key}",
                                          "Content-Type": "application/json"})
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise RuntimeError(f"Voyage API {resp.status_code}: {resp.text[:300]}")
        raise RuntimeError("Voyage API 重試用盡")


def get_embedder() -> Embedder:
    provider = os.getenv("EMBEDDING_PROVIDER", "voyage").lower()
    if provider == "fake":
        return FakeEmbedder()
    if provider == "voyage":
        return VoyageEmbedder()
    raise RuntimeError(f"不認得的 EMBEDDING_PROVIDER={provider!r}（只有 voyage / fake）")
