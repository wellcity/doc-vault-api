"""
DocVault 資料庫模組
PostgreSQL + pgvector：向量儲存 + 權限管理 + metadata
"""
import sys
sys.path.append(".")

import logging
import psycopg2
from psycopg2.extras import execute_values
from contextlib import contextmanager
from config import (
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_DB,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
    POSTGRES_TIMEZONE,
    LOCAL_EMBEDDING_DIM,
)

logger = logging.getLogger(__name__)

_DSN = f"host={POSTGRES_HOST} port={POSTGRES_PORT} dbname={POSTGRES_DB} user={POSTGRES_USER} password={POSTGRES_PASSWORD}"


@contextmanager
def get_conn():
    """取得資料庫連線（自動還原）"""
    conn = psycopg2.connect(_DSN)
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE %s", (POSTGRES_TIMEZONE,))
    try:
        yield conn
    finally:
        conn.close()


def init_db(vector_dim: int = None):
    """初始化資料庫 schema（確保 tables / vector extension 存在）"""
    logger.info(
        "init_db 開始：db=%s@%s:%s, user=%s",
        POSTGRES_DB,
        POSTGRES_HOST,
        POSTGRES_PORT,
        POSTGRES_USER,
    )
    if vector_dim is None:
        logger.info("未指定 vector_dim，嘗試由 Embedding Provider 動態取得維度")
        try:
            from embeddings import get_embedding_provider
            provider = get_embedding_provider()
            logger.info("Embedding Provider 類型：%s", provider.__class__.__name__)
            vector_dim = provider.dimension()
            logger.info("Embedding 維度偵測成功：dim=%s", vector_dim)
        except Exception:
            logger.exception("Embedding 維度偵測失敗，init_db 中止")
            raise
    else:
        logger.info("使用外部指定 vector_dim：dim=%s", vector_dim)

    try:
        with get_conn() as conn:
            logger.info("PostgreSQL 連線成功，開始套用 schema")
            with conn.cursor() as cur:
                # pgvector extension
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

                # 文件主表
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS documents (
                        file_id        VARCHAR(64) PRIMARY KEY,
                        content_hash   VARCHAR(64),     -- SHA256 hash 去重
                        filename       VARCHAR(256) NOT NULL,
                        file_type      VARCHAR(16) NOT NULL,
                        confidentiality VARCHAR(32) DEFAULT '公開',
                        department     VARCHAR(64),
                        metadata_json  TEXT DEFAULT '{}',
                        created_at    TIMESTAMP DEFAULT NOW()
                    )
                """)
                # 舊資料庫升級：補齊去重與 metadata 欄位（若不存在）
                cur.execute("""
                    ALTER TABLE documents
                    ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64)
                """)
                cur.execute("""
                    ALTER TABLE documents
                    ADD COLUMN IF NOT EXISTS metadata_json TEXT DEFAULT '{}'
                """)
                # content_hash 索引（加速去重查詢）
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_documents_content_hash
                    ON documents(content_hash)
                """)

                # 動態建立 chunks 表（帶正確維度）
                cur.execute("""
                    SELECT 1 FROM pg_tables
                    WHERE tablename = 'document_chunks'
                """)
                if not cur.fetchone():
                    cur.execute(f"""
                        CREATE TABLE document_chunks (
                            chunk_id      VARCHAR(64) PRIMARY KEY,
                            file_id       VARCHAR(64) REFERENCES documents(file_id) ON DELETE CASCADE,
                            page          INTEGER DEFAULT 0,
                            chunk_index   INTEGER DEFAULT 0,
                            text          TEXT NOT NULL,
                            image_paths   TEXT[] DEFAULT '{{}}',
                            text_vector   VECTOR({vector_dim}),
                            created_at    TIMESTAMP DEFAULT NOW()
                        )
                    """)
                    # pgvector 的 vector 型別在目前版本下，ivfflat / hnsw 對維度都有上限（通常 2000）。
                    # 若模型維度更高（例如 4096），先不建立向量索引，仍可做精確搜尋（速度較慢）。
                    if vector_dim > 2000:
                        logger.warning(
                            "向量維度 %s 超過 pgvector ANN 索引上限，跳過 idx_chunks_vector 建立；"
                            "搜尋將使用無索引精確比對。",
                            vector_dim,
                        )
                    else:
                        cur.execute("""
                            CREATE INDEX idx_chunks_vector
                            ON document_chunks
                            USING ivfflat (text_vector vector_cosine_ops)
                            WITH (lists = 100)
                        """)

                # 使用者權限表
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS document_permissions (
                        id           SERIAL PRIMARY KEY,
                        user_id      VARCHAR(64) NOT NULL,
                        document_id  VARCHAR(64) REFERENCES documents(file_id) ON DELETE CASCADE,
                        access_level VARCHAR(16) DEFAULT 'read',
                        granted_at   TIMESTAMP DEFAULT NOW(),
                        UNIQUE(user_id, document_id)
                    )
                """)

                # file_id 索引（加速權限過濾）
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_permissions_user
                    ON document_permissions(user_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chunks_file_id
                    ON document_chunks(file_id)
                """)

                conn.commit()
                logger.info("資料庫初始化完成（PostgreSQL + pgvector, dim=%s）", vector_dim)
    except Exception:
        logger.exception("資料庫初始化失敗")
        raise
