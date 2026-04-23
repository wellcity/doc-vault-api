"""
Embedding 提供者工廠
支援 openai / ollama / local (sentence-transformers)
"""
import sys
sys.path.append(".")

import logging
import os
from abc import ABC, abstractmethod
from config import EMBEDDING_PROVIDER, OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_EMBEDDING_MODEL, \
    OLLAMA_BASE_URL, OLLAMA_EMBEDDING_MODEL, \
    LOCAL_EMBEDDING_MODEL, LOCAL_EMBEDDING_DIM

logger = logging.getLogger(__name__)

_EmbeddingProvider = None  # singleton


def get_embedding_provider() -> "EmbeddingProvider":
    global _EmbeddingProvider
    if _EmbeddingProvider is None:
        _EmbeddingProvider = _build_provider()
    return _EmbeddingProvider


def _build_provider() -> "EmbeddingProvider":
    provider = EMBEDDING_PROVIDER.lower()
    logger.info(f"初始化 Embedding Provider：{provider}")

    if provider == "openai":
        # LM Studio（OpenAI-compatible）通常不需要真實 API key，
        # 只要有 OPENAI_BASE_URL 即可用占位 key。
        if not OPENAI_API_KEY and not OPENAI_BASE_URL:
            raise ValueError("EMBEDDING_PROVIDER=openai 但未設定 OPENAI_API_KEY")
        api_key = OPENAI_API_KEY or "lm-studio"
        return OpenAIEmbeddingProvider(api_key, OPENAI_EMBEDDING_MODEL, OPENAI_BASE_URL or None)

    elif provider == "ollama":
        return OllamaEmbeddingProvider(OLLAMA_BASE_URL, OLLAMA_EMBEDDING_MODEL)

    elif provider == "local":
        return LocalEmbeddingProvider(LOCAL_EMBEDDING_MODEL, LOCAL_EMBEDDING_DIM)

    else:
        raise ValueError(f"不支援的 EMBEDDING_PROVIDER：{provider}，支援：openai, ollama, local")


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """將文字列表轉為向量列表"""
        pass

    @abstractmethod
    def dimension(self) -> int:
        """回傳向量維度"""
        pass


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        from openai import OpenAI
        import httpx

        timeout_seconds = float(os.getenv("OPENAI_EMBEDDING_TIMEOUT_SECONDS", "120"))

        # 這裡特別關閉 trust_env，避免環境變數（例如 http_proxy/https_proxy）
        # 影響到連到 LM Studio 的行為，導致連線被強制中斷。
        http_client = httpx.Client(timeout=timeout_seconds, trust_env=False)
        logger.info("OpenAI Embedding timeout 設定：%s 秒", timeout_seconds)

        if base_url:
            self.client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
        else:
            self.client = OpenAI(api_key=api_key, http_client=http_client)
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in resp.data]

    def dimension(self) -> int:
        # 動態取得向量維度，避免不同 embedding 模型輸出維度不一致。
        if not hasattr(self, "_dim"):
            vectors = self.embed(["dim"])
            self._dim = len(vectors[0]) if vectors and vectors[0] else 0
        return self._dim


class OllamaEmbeddingProvider(EmbeddingProvider):
    def __init__(self, base_url: str, model: str):
        import httpx
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(timeout=60.0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批次取得 embedding，減少 HTTP 往返（使用執行緒池並發）"""
        import concurrent.futures

        def _single(text: str) -> list[float]:
            resp = self._client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
            )
            resp.raise_for_status()
            return resp.json()["embedding"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(_single, text): i for i, text in enumerate(texts)}
            results = [None] * len(texts)
            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()
        return results

    def dimension(self) -> int:
        if not hasattr(self, "_dim"):
            resp = self._client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": "dim"},
            )
            self._dim = len(resp.json()["embedding"])
        return self._dim


class LocalEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model_name: str, dim: int):
        from sentence_transformers import SentenceTransformer
        logger.info(f"載入本地模型：{model_name}（CPU）")
        self.model = SentenceTransformer(model_name, device="cpu")
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, convert_to_numpy=True).tolist()

    def dimension(self) -> int:
        return self._dim
