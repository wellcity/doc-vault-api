"""
驗證「文件 → 解析 → 向量化 → insert_chunks」流程（不連真實 LM Studio / PostgreSQL）。
"""
from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
from unittest.mock import MagicMock

import pytest
from reportlab.pdfgen import canvas
from starlette.testclient import TestClient


def _minimal_pdf_bytes() -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, "DocVault vector test — 最小 PDF 內容。")
    c.save()
    return buf.getvalue()


class FakeEmbeddingProvider:
    """固定維度向量，供斷言是否完成向量化。"""

    def __init__(self, dim: int = 8):
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.125] * self._dim for _ in texts]

    def dimension(self) -> int:
        return self._dim


class FakeCursor:
    def execute(self, *args, **kwargs):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        return None


class FakeConn:
    def cursor(self):
        return FakeCursor()

    def commit(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        return None


@contextmanager
def fake_get_conn():
    yield FakeConn()


@pytest.fixture
def captured_chunks():
    return []


@pytest.fixture
def client(monkeypatch, captured_chunks):
    import main

    monkeypatch.setattr(main, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(main, "get_conn", fake_get_conn)

    def record_insert(chunks: list[dict]) -> int:
        captured_chunks.extend(chunks)
        return len(chunks)

    monkeypatch.setattr(main, "insert_chunks", record_insert)
    monkeypatch.setattr(
        main,
        "get_embedding_provider",
        lambda: FakeEmbeddingProvider(dim=8),
    )

    with TestClient(main.app, raise_server_exceptions=True) as c:
        yield c


def test_ingest_pdf_produces_chunks_with_embeddings(client, captured_chunks):
    """上傳 PDF 後，每個 chunk 應帶有與 FakeEmbeddingProvider 相同維度的向量。"""
    pdf_bytes = _minimal_pdf_bytes()
    files = {"file": ("test_vector.pdf", pdf_bytes, "application/pdf")}
    resp = client.post("/ingest", files=files)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["chunk_count"] >= 1

    assert len(captured_chunks) == body["chunk_count"]
    for ch in captured_chunks:
        assert "embedding" in ch
        assert len(ch["embedding"]) == 8
        assert ch["text"].strip()


def test_ingest_rejects_unsupported_type(client):
    resp = client.post(
        "/ingest",
        files={"file": ("x.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
