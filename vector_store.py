import sys
sys.path.append(".")

from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType, utility
from config import MILVUS_HOST, MILVUS_PORT
from embeddings import get_embedding_provider
import logging

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "document_chunks"


def get_connection():
    """確保 Milvus 連線"""
    if not connections.has_connection("default"):
        connections.connect("default", host=MILVUS_HOST, port=str(MILVUS_PORT))
        logger.info(f"已連線至 Milvus ({MILVUS_HOST}:{MILVUS_PORT})")


def ensure_collection():
    """確保 Collection 存在，若不存在則建立"""
    get_connection()

    if utility.has_collection(_COLLECTION_NAME):
        collection = Collection(_COLLECTION_NAME)
        collection.load()
        logger.info(f"Collection '{_COLLECTION_NAME}' 已存在")
        return collection

    # 動態取得向量維度
    embed = get_embedding_provider()
    vector_dim = embed.dimension()
    logger.info(f"Embedding 維度：{vector_dim}")

    fields = [
        FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=64, is_primary_key=True),
        FieldSchema(name="file_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="source_file", dtype=DataType.VARCHAR, max_length=256),
        FieldSchema(name="file_type", dtype=DataType.VARCHAR, max_length=16),
        FieldSchema(name="page", dtype=DataType.INT32),
        FieldSchema(name="chunk_index", dtype=DataType.INT32),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=8192),
        FieldSchema(name="image_paths", dtype=DataType.ARRAY, element_type=DataType.VARCHAR, max_length=512, max_capacity=10),
        FieldSchema(name="metadata", dtype=DataType.VARCHAR, max_length=1024),
        FieldSchema(name="text_vector", dtype=DataType.FLOAT_VECTOR, dim=vector_dim),
        FieldSchema(name="created_at", dtype=DataType.VARCHAR, max_length=32),
    ]

    schema = CollectionSchema(fields=fields, description="Document chunks with embeddings")
    collection = Collection(name=_COLLECTION_NAME, schema=schema)

    # 建立 HNSW index
    index_params = {
        "index_type": "HNSW",
        "metric_type": "COSINE",
        "params": {"M": 16, "efConstruction": 200}
    }
    collection.create_index(field_name="text_vector", index_params=index_params)
    collection.load()

    logger.info(f"Collection '{_COLLECTION_NAME}' 已建立（HNSW index）")
    return collection


def insert_chunks(chunks: list[dict]) -> int:
    """寫入多個 chunks，回傳寫入數量"""
    collection = ensure_collection()

    data = [
        [c["chunk_id"] for c in chunks],
        [c["file_id"] for c in chunks],
        [c["source_file"] for c in chunks],
        [c["file_type"] for c in chunks],
        [c["page"] for c in chunks],
        [c["chunk_index"] for c in chunks],
        [c["text"] for c in chunks],
        [c.get("image_paths", []) for c in chunks],
        [c.get("metadata_json", "{}") for c in chunks],
        [c["embedding"] for c in chunks],
        [c["created_at"] for c in chunks],
    ]

    collection.insert(data)
    collection.flush()
    logger.info(f"已寫入 {len(chunks)} 個 chunks")
    return len(chunks)


def search(query_vector: list[float], top_k: int = 10, file_types: list[str] | None = None) -> list[dict]:
    """向量相似度搜尋"""
    collection = ensure_collection()

    filter_expr = None
    if file_types:
        type_list = ", ".join(f'"{ft}"' for ft in file_types)
        filter_expr = f"file_type in [{type_list}]"

    results = collection.search(
        data=[query_vector],
        anns_field="text_vector",
        param={"metric_type": "COSINE", "params": {"ef": 128}},
        limit=top_k,
        output_fields=["chunk_id", "file_id", "source_file", "file_type", "page", "chunk_index", "text", "image_paths", "metadata", "created_at"],
        filter=filter_expr,
    )

    hits = []
    for hits_slice in results:
        for hit in hits_slice:
            record = {
                "chunk_id": hit.entity.get("chunk_id"),
                "file_id": hit.entity.get("file_id"),
                "source_file": hit.entity.get("source_file"),
                "file_type": hit.entity.get("file_type"),
                "page": hit.entity.get("page"),
                "chunk_index": hit.entity.get("chunk_index"),
                "text": hit.entity.get("text"),
                "image_paths": hit.entity.get("image_paths") or [],
                "metadata": hit.entity.get("metadata"),
                "created_at": hit.entity.get("created_at"),
                "score": hit.score,
            }
            hits.append(record)

    return hits


def get_chunks_by_ids(chunk_ids: list[str]) -> list[dict]:
    """依 chunk_id 查詢完整資料"""
    collection = ensure_collection()

    id_list = ", ".join(f'"{cid}"' for cid in chunk_ids)
    filter_expr = f"chunk_id in [{id_list}]"
    results = collection.query(
        expr=filter_expr,
        output_fields=["chunk_id", "file_id", "source_file", "file_type", "page", "chunk_index", "text", "image_paths", "metadata", "created_at"],
    )
    return results


def delete_by_file_id(file_id: str) -> int:
    """刪除指定檔案的所有 chunks"""
    collection = ensure_collection()
    expr = f'file_id == "{file_id}"'
    collection.delete(expr)
    collection.flush()
    logger.info(f"已刪除檔案 {file_id} 的所有 chunks")
    return 1


def get_stats() -> dict:
    """取得統計資訊"""
    collection = ensure_collection()
    stats = {
        "collection": _COLLECTION_NAME,
        "row_count": collection.num_entities,
    }
    return stats
