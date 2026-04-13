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
from config import POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, LOCAL_EMBEDDING_DIM

logger = logging.getLogger(__name__)

_DSN = f"host={POSTGRES_HOST} port={POSTGRES_PORT} dbname={POSTGRES_DB} user={POSTGRES_USER} password={POSTGRES_PASSWORD}"


@contextmanager
def get_conn():
    """取得資料庫連線（自動還原）"""
    conn = psycopg2.connect(_DSN)
    try:
        yield conn
    finally:
        conn.close()


def init_db(vector_dim: int = None):
    """初始化資料庫 schema（確保 tables / vector extension 存在）"""
    if vector_dim is None:
        from embeddings import get_embedding_provider
        vector_dim = get_embedding_provider().dimension()

    with get_conn() as conn:
        with conn.cursor() as cur:
            # pgvector extension
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

            # 文件主表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    file_id        VARCHAR(64) PRIMARY KEY,
                    filename       VARCHAR(256) NOT NULL,
                    file_type      VARCHAR(16) NOT NULL,
                    confidentiality VARCHAR(32) DEFAULT '公開',
                    department     VARCHAR(64),
                    metadata_json  TEXT DEFAULT '{}',
                    created_at    TIMESTAMP DEFAULT NOW()
                )
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
                # IVFFlat 索引（效能較 HNSW 好建立）
                cur.execute(f"""
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
            logger.info(f"資料庫初始化完成（PostgreSQL + pgvector, dim={vector_dim}）")
