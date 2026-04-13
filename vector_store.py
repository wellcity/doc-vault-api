"""
向量儲存（PostgreSQL + pgvector）
"""
import sys
sys.path.append(".")

import logging
from typing import Optional
from db import get_conn
from embeddings import get_embedding_provider

logger = logging.getLogger(__name__)


def insert_chunks(chunks: list[dict]) -> int:
    """寫入 chunks 到 PostgreSQL"""
    if not chunks:
        return 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            for chunk in chunks:
                vec = chunk.get("embedding")
                vec_literal = f"[{','.join(str(v) for v in vec)}]" if vec else None

                cur.execute("""
                    INSERT INTO document_chunks
                        (chunk_id, file_id, page, chunk_index, text, image_paths, text_vector, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s)
                    ON CONFLICT (chunk_id) DO UPDATE
                        SET text = EXCLUDED.text,
                            image_paths = EXCLUDED.image_paths,
                            text_vector = EXCLUDED.text_vector
                """, (
                    chunk["chunk_id"],
                    chunk["file_id"],
                    chunk.get("page", 0),
                    chunk.get("chunk_index", 0),
                    chunk["text"],
                    chunk.get("image_paths", []),
                    vec_literal,
                    chunk.get("created_at"),
                ))
            conn.commit()

    logger.info(f"已寫入 {len(chunks)} 個 chunks")
    return len(chunks)


def search(
    query_vector: list[float],
    top_k: int = 10,
    user_id: Optional[str] = None,
    file_types: list[str] | None = None,
    confidentiality: list[str] | None = None,
) -> list[dict]:
    """
    向量相似度搜尋（可選：依 user_id 過濾權限）
    """
    # 動態取得維度
    embed = get_embedding_provider()
    dim = embed.dimension()
    vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"

    # 基本查詢
    sql_parts = ["SELECT c.chunk_id, c.file_id, c.page, c.chunk_index, c.text,"]
    sql_parts.append("       c.image_paths, d.filename, d.confidentiality,")
    sql_parts.append("       (c.text_vector <=> %(vec)s::vector) AS cosine_dist")
    sql_parts.append("FROM document_chunks c")
    sql_parts.append("JOIN documents d ON c.file_id = d.file_id")

    conditions = []
    params = {"vec": vec_str, "limit": top_k}

    # 權限過濾
    if user_id:
        sql_parts.append("WHERE c.file_id IN (")
        sql_parts.append("    SELECT dp.document_id FROM document_permissions dp")
        sql_parts.append("    WHERE dp.user_id = %(user_id)s")
        sql_parts.append(")")
        params["user_id"] = user_id

    # 檔案類型過濾
    if file_types:
        placeholder = ",".join(f"%(ft{i})s" for i in range(len(file_types)))
        conditions.append(f"d.file_type IN ({placeholder})")
        for i, ft in enumerate(file_types):
            params[f"ft{i}"] = ft

    # 機密等級過濾
    if confidentiality:
        placeholder = ",".join(f"%(conf{i})s" for i in range(len(confidentiality)))
        conditions.append(f"d.confidentiality IN ({placeholder})")
        for i, c in enumerate(confidentiality):
            params[f"conf{i}"] = c

    if conditions:
        if user_id:
            sql_parts.append("  AND " + " AND ".join(conditions))
        else:
            sql_parts.append("WHERE " + " AND ".join(conditions))

    sql_parts.append(f"ORDER BY cosine_dist ASC LIMIT %(limit)s")

    query = "\n".join(sql_parts)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    results = []
    for row in rows:
        results.append({
            "chunk_id": row[0],
            "file_id": row[1],
            "page": row[2],
            "chunk_index": row[3],
            "text": row[4],
            "image_paths": row[5] or [],
            "metadata": row[6] if len(row) > 6 else {},
            "filename": row[7],
            "confidentiality": row[8],
            "score": 1 - float(row[9]),  # cosine_dist 越小越相似
        })

    return results


def get_chunks_by_ids(chunk_ids: list[str]) -> list[dict]:
    """依 chunk_id 查詢完整資料"""
    if not chunk_ids:
        return []

    placeholders = ",".join(["%s"] * len(chunk_ids))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT c.chunk_id, c.file_id, c.page, c.chunk_index, c.text,
                       c.image_paths, c.metadata, d.filename, d.confidentiality
                FROM document_chunks c
                JOIN documents d ON c.file_id = d.file_id
                WHERE c.chunk_id IN ({placeholders})
            """, chunk_ids)
            rows = cur.fetchall()

    results = []
    for row in rows:
        results.append({
            "chunk_id": row[0],
            "file_id": row[1],
            "page": row[2],
            "chunk_index": row[3],
            "text": row[4],
            "image_paths": row[5] or [],
            "metadata": row[6],
            "filename": row[7],
            "confidentiality": row[8],
        })
    return results


def get_stats() -> dict:
    """統計資訊"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM documents")
            doc_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM document_chunks")
            chunk_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT user_id) FROM document_permissions")
            user_count = cur.fetchone()[0]
    return {
        "documents": doc_count,
        "chunks": chunk_count,
        "users": user_count,
    }
