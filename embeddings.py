"""
Embedding 提供者工廠
支援 openai / ollama / local (sentence-transformers)
"""
import sys
sys.path.append(".")

import logging
from abc import ABC, abstractmethod
from config import EMBEDDING_PROVIDER, OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL, \
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
        if not OPENAI_API_KEY:
            raise ValueError("EMBEDDING_PROVIDER=openai 但未設定 OPENAI_API_KEY")
        return OpenAIEmbeddingProvider(OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL)

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
    def __init__(self, api_key: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in resp.data]

    def dimension(self) -> int:
        # text-embedding-3-small = 1536
        dims = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072, "text-embedding-ada-002": 1536}
        return dims.get(self.model, 1536)


class OllamaEmbeddingProvider(EmbeddingProvider):
    def __init__(self, base_url: str, model: str):
        import httpx
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(timeout=60.0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx
        vectors = []
        for text in texts:
            resp = self._client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
            )
            resp.raise_for_status()
            vectors.append(resp.json()["embedding"])
        return vectors

    def dimension(self) -> int:
        # 先呼叫一次取得維度（快取）
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
        logger.info(f"載入本地模型：{model_name}")
        self.model = SentenceTransformer(model_name)
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, convert_to_numpy=True).tolist()

    def dimension(self) -> int:
        return self._dim
