"""
DocVault FastAPI - 文件向量資料庫 API
PostgreSQL + pgvector
"""
import sys
sys.path.append(".")

import os
import io
import uuid
import time
import json
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import quote
from urllib.request import urlopen

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import nest_asyncio
import psycopg2

try:
    nest_asyncio.apply()
except Exception:
    pass

from config import (
    API_HOST,
    API_PORT,
    IMAGE_DIR,
    OUTPUT_DIR,
    EMBEDDING_PROVIDER,
    OPENAI_BASE_URL,
    OLLAMA_BASE_URL,
    ORACLE_HOST,
    ORACLE_PORT,
    ORACLE_SERVICE_NAME,
    ORACLE_SID,
    ORACLE_DSN,
    ORACLE_CLIENT_LIB_DIR,
    ORACLE_USE_THICK_MODE,
    ORACLE_USER,
    ORACLE_PASSWORD,
)
from db import init_db, get_conn
from vector_store import insert_chunks, search as pg_search, get_chunks_by_ids, get_stats
from parsers.pdf_parser import parse_pdf
from parsers.word_parser import parse_docx
from parsers.ppt_parser import parse_pptx
from parsers.excel_parser import parse_xlsx
from ppt_generator import generate_ppt, generate_ppt_from_outline
from pdf_generator import generate_pdf_from_outline, generate_pdf_from_chunks
from excel_generator import generate_excel_from_data
from word_generator import generate_word_from_outline
from embeddings import get_embedding_provider
from scraper import scrape as do_scrape

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "16"))
EMBEDDING_BATCH_MAX_RETRIES = int(os.getenv("EMBEDDING_BATCH_MAX_RETRIES", "2"))
EMBEDDING_BATCH_RETRY_BACKOFF_MS = int(os.getenv("EMBEDDING_BATCH_RETRY_BACKOFF_MS", "800"))
EMBEDDING_BATCH_MIN_SIZE = int(os.getenv("EMBEDDING_BATCH_MIN_SIZE", "2"))
EMBEDDING_MAX_TEXT_LENGTH = int(os.getenv("EMBEDDING_MAX_TEXT_LENGTH", "6000"))
SQL_BATCH_CONCURRENCY = max(1, int(os.getenv("SQL_BATCH_CONCURRENCY", "4")))
SQL_BATCH_CHUNK_SIZE = max(1, int(os.getenv("SQL_BATCH_CHUNK_SIZE", "20")))
SQL_BATCH_ROW_MAX_RETRIES = max(0, int(os.getenv("SQL_BATCH_ROW_MAX_RETRIES", "2")))
SQL_BATCH_ROW_RETRY_BACKOFF_MS = max(100, int(os.getenv("SQL_BATCH_ROW_RETRY_BACKOFF_MS", "600")))
SQL_BATCH_DOWNLOAD_TIMEOUT_SEC = max(1, int(os.getenv("SQL_BATCH_DOWNLOAD_TIMEOUT_SEC", "30")))
SEARCH_PROTECT_ENABLED = os.getenv("SEARCH_PROTECT_ENABLED", "true").lower() == "true"
SEARCH_PROTECT_THRESHOLD = max(1, int(os.getenv("SEARCH_PROTECT_THRESHOLD", "1")))
SEARCH_PROTECT_INGEST_CONCURRENCY = max(1, int(os.getenv("SEARCH_PROTECT_INGEST_CONCURRENCY", "2")))
BATCH_JOB_TTL_SECONDS = max(60, int(os.getenv("BATCH_JOB_TTL_SECONDS", "21600")))

SQL_BATCH_JOBS: dict[str, dict] = {}
ACTIVE_SEARCH_REQUESTS = 0


# ======================== 生命週期 ========================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("DocVault API 啟動中：host=%s, port=%s", API_HOST, API_PORT)
    logger.info(
        "啟動設定：embedding_provider=%s, openai_base_url=%s, ollama_base_url=%s",
        EMBEDDING_PROVIDER,
        OPENAI_BASE_URL,
        OLLAMA_BASE_URL,
    )
    logger.info("開始初始化資料庫...")
    try:
        init_db()
        logger.info("資料庫初始化成功，API 準備就緒")
    except Exception:
        logger.exception("資料庫初始化警告：啟動時初始化失敗，服務仍會啟動")
    yield
    logger.info("DocVault API 關閉")


# ======================== FastAPI 應用 ========================

app = FastAPI(
    title="DocVault API",
    description="文件向量資料庫 — 入庫、搜尋、PPT 匯出（PostgreSQL + pgvector）",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Admin web 管理介面
from admin_routes import router as admin_router
app.include_router(admin_router)


# ======================== 模型 ========================

class SearchRequest(BaseModel):
    query: str
    top_k: int = 10
    file_types: list[str] | None = None
    confidentiality: list[str] | None = None
    user_id: str | None = None  # JWT user_id


class PermissionSyncRequest(BaseModel):
    user_id: str
    documents: list[dict]  # [{"document_id": "xxx", "access_level": "read"}]


class ExportPptRequest(BaseModel):
    result_ids: list[str]
    output_name: str = "report"
    include_images: bool = True


class GeneratePptFromOutlineRequest(BaseModel):
    outline: dict
    output_name: str = "presentation"


class GeneratePdfFromOutlineRequest(BaseModel):
    outline: dict
    output_name: str = "report"


class GeneratePdfFromChunksRequest(BaseModel):
    result_ids: list[str]
    output_name: str = "document"


class GenerateExcelRequest(BaseModel):
    sheets: list[dict]
    output_name: str = "report"
    title: str | None = None


class GenerateWordRequest(BaseModel):
    outline: dict
    output_name: str = "document"


class ScrapeRequest(BaseModel):
    url: str
    selector: str | None = None  # CSS 選擇器，只取符合的元素
    extract_links: bool = False   # 是否一併回傳連結清單
    timeout: float = 30.0        # 請求逾時（秒）

class SqlBatchIngestRequest(BaseModel):
    apikey: str
    num: int = -1


# ======================== 工具函式 ========================

def parse_file(file_path: str, file_id: str, file_ext: str, metadata: dict):
    parsers = {
        ".pdf": parse_pdf,
        ".docx": parse_docx,
        ".pptx": parse_pptx,
        ".xlsx": parse_xlsx,
    }
    parser = parsers.get(file_ext.lower())
    if not parser:
        raise ValueError(f"不支援的格式：{file_ext}")
    return parser(file_path, file_id, metadata)


def get_file_ext(filename: str) -> str:
    _, ext = os.path.splitext(filename)
    return ext.lower()

SQL_BATCH_API_KEY = "docvault-batch-ingest-key"
SQL_BATCH_FILE_BASE_URL = "http://giscnwebwf01/TKM"
SQL_BATCH_SOURCE_QUERY = """
    SELECT
        b.attachment_id AS file_path,
        b.file_name,
        a.*
    FROM km_document a
    LEFT JOIN doc_attachement b ON a.ref_no = b.ref_no
    WHERE a.station_no = 99 and a.secretlevel = '一般'
"""

def _fetch_sql_batch_source_rows() -> tuple[list[tuple], list[str]]:
    """從 Oracle 取得 SQL 批次來源資料"""
    if not all([ORACLE_USER, ORACLE_PASSWORD]):
        raise HTTPException(
            status_code=500,
            detail={"error": "Oracle 連線設定不完整，請設定 ORACLE_USER/ORACLE_PASSWORD"},
        )

    try:
        import oracledb
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail={"error": "缺少 Oracle driver，請安裝 oracledb 套件"},
        )

    if ORACLE_USE_THICK_MODE:
        try:
            init_kwargs = {}
            if ORACLE_CLIENT_LIB_DIR:
                init_kwargs["lib_dir"] = ORACLE_CLIENT_LIB_DIR
            oracledb.init_oracle_client(**init_kwargs)
        except Exception as e:
            msg = str(e)
            if "DPI-1047" in msg:
                raise HTTPException(
                    status_code=500,
                    detail={"error": f"Oracle Thick Mode 初始化失敗：{msg}", "hint": "請確認已安裝 Oracle Instant Client，並正確設定 ORACLE_CLIENT_LIB_DIR"},
                )
            if "has already been initialized" not in msg:
                raise HTTPException(status_code=500, detail={"error": f"Oracle Thick Mode 初始化失敗：{msg}"})

    if ORACLE_DSN:
        dsn = ORACLE_DSN
    elif ORACLE_HOST and ORACLE_SERVICE_NAME:
        dsn = oracledb.makedsn(ORACLE_HOST, ORACLE_PORT, service_name=ORACLE_SERVICE_NAME)
    elif ORACLE_HOST and ORACLE_SID:
        dsn = oracledb.makedsn(ORACLE_HOST, ORACLE_PORT, sid=ORACLE_SID)
    else:
        raise HTTPException(
            status_code=500,
            detail={"error": "Oracle 連線設定不完整，請設定 ORACLE_DSN 或 ORACLE_HOST 搭配 ORACLE_SERVICE_NAME/ORACLE_SID"},
        )

    try:
        with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(SQL_BATCH_SOURCE_QUERY)
                rows = cur.fetchall()
                columns = [desc[0] for desc in cur.description] if cur.description else []
        return rows, columns
    except Exception as e:
        msg = str(e)
        hint = "請確認 ORACLE_SERVICE_NAME 或 ORACLE_SID 是否正確（不要填 DEDICATED），並確認 listener 已註冊該服務"
        if "DPY-6001" in msg or "ORA-12514" in msg:
            raise HTTPException(status_code=500, detail={"error": f"Oracle 連線失敗：{msg}", "hint": hint})
        if "DPY-3010" in msg:
            raise HTTPException(
                status_code=500,
                detail={"error": f"Oracle 連線失敗：{msg}", "hint": "此 Oracle 版本需啟用 Thick Mode，請設定 ORACLE_USE_THICK_MODE=true 並安裝 Oracle Instant Client"},
            )
        raise HTTPException(status_code=500, detail={"error": f"Oracle 連線失敗：{msg}"})


def _ingest_file_content_sync(filename: str, content: bytes, metadata: dict | None = None) -> dict:
    """重用 /ingest 的核心邏輯：以檔名 + bytes 完成入庫（同步執行）"""
    start = time.time()
    stage_start = start
    import hashlib

    meta = metadata or {}
    logger.info("ingest 開始：filename=%s", filename)

    # 副檔名檢查（.doc 透過 LibreOffice 轉換為 .docx 後解析）
    file_ext = get_file_ext(filename or "unknown")
    if file_ext not in {".pdf", ".docx", ".ppt", ".pptx", ".xlsx"}:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Unsupported file type",
                "supported": [".pdf", ".docx", ".ppt", ".pptx", ".xlsx"],
            },
        )

    # 計算 content_hash（用於去重）
    content_hash = hashlib.sha256(content).hexdigest()[:32]
    logger.info("ingest 階段完成：讀檔與計算 hash，elapsed_ms=%s", int((time.time() - stage_start) * 1000))
    stage_start = time.time()
    file_id = str(uuid.uuid4())
    confidentiality = meta.get("confidentiality", "公開")
    department = meta.get("department", "")

    # 先查是否已有相同 content_hash 的檔案（去重）
    duplicated_file_id: str | None = None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_id FROM documents WHERE content_hash = %s LIMIT 1",
                (content_hash,),
            )
            row = cur.fetchone()
            if row:
                file_id = row[0]
                duplicated_file_id = file_id
                logger.info(f"發現重複檔案，複用 file_id={file_id}")
            else:
                # 寫入 documents 表
                cur.execute("""
                    INSERT INTO documents (file_id, content_hash, filename, file_type, confidentiality, department, metadata_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (file_id) DO UPDATE
                        SET filename = EXCLUDED.filename,
                            confidentiality = EXCLUDED.confidentiality,
                            department = EXCLUDED.department,
                            metadata_json = EXCLUDED.metadata_json,
                            content_hash = EXCLUDED.content_hash
                """, (file_id, content_hash, filename or "unknown", file_ext.lstrip("."), confidentiality, department, json.dumps(meta)))

                # 舊格式相容：document_id
                doc_id = meta.get("document_id")
                if doc_id and doc_id != file_id:
                    cur.execute("UPDATE documents SET file_id = %s WHERE file_id = %s", (doc_id, file_id))
                    file_id = doc_id

                conn.commit()
    logger.info("ingest 階段完成：documents 去重與寫入，elapsed_ms=%s", int((time.time() - stage_start) * 1000))
    stage_start = time.time()

    # 若同 content_hash 文件已存在且已有 chunks，直接回傳避免重複建立
    if duplicated_file_id:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM document_chunks WHERE file_id = %s",
                    (duplicated_file_id,),
                )
                existing_chunk_count = cur.fetchone()[0]
        if existing_chunk_count > 0:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.info(
                "ingest 重複檔案略過重建：file_id=%s, existing_chunks=%s",
                duplicated_file_id,
                existing_chunk_count,
            )
            return {
                "file_id": duplicated_file_id,
                "filename": filename,
                "file_type": file_ext.lstrip("."),
                "confidentiality": confidentiality,
                "status": "completed",
                "chunk_count": existing_chunk_count,
                "image_count": 0,
                "processing_time_ms": elapsed_ms,
                "deduplicated": True,
            }

    # 儲存暫存檔
    temp_dir = Path("/tmp/docvault")
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{file_id}{file_ext}"
    converted_docx = None  # 追蹤轉換後的 .docx 暫存檔
    converted_pptx = None  # 追蹤轉換後的 .pptx 暫存檔

    try:
        with open(temp_path, "wb") as f:
            f.write(content)

        # .doc 格式：透過 LibreOffice 轉換為 .docx 再解析
        if file_ext == ".doc":
            from parsers.convert_doc import convert_doc_to_docx
            try:
                converted_docx = convert_doc_to_docx(str(temp_path))
                logger.info(f".doc 轉換成功：{converted_docx}")
                # 更新解析路徑與副檔名
                temp_path = Path(converted_docx)
                file_ext = ".docx"
            except FileNotFoundError as e:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": "LibreOffice not found",
                        "message": str(e),
                        "hint": "請在 Windows 伺服器上安裝 LibreOffice：https://www.libreoffice.org/download/download/",
                    },
                )
            except RuntimeError as e:
                raise HTTPException(status_code=400, detail={"error": f".doc 轉換失敗：{str(e)}"})
        if file_ext == ".ppt":
            from parsers.convert_doc import convert_ppt_to_pptx
            try:
                converted_pptx = convert_ppt_to_pptx(str(temp_path))
                logger.info(f".ppt 轉換成功：{converted_pptx}")
                temp_path = Path(converted_pptx)
                file_ext = ".pptx"
            except FileNotFoundError as e:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": "LibreOffice not found",
                        "message": str(e),
                        "hint": "請在 Windows 伺服器上安裝 LibreOffice：https://www.libreoffice.org/download/download/",
                    },
                )
            except RuntimeError as e:
                raise HTTPException(status_code=400, detail={"error": f".ppt 轉換失敗：{str(e)}"})

        # 解析
        chunks, image_paths = parse_file(str(temp_path), file_id, file_ext, meta)
        chunk_count = len(chunks)
        image_count = len(image_paths)
        logger.info(
            "ingest 階段完成：文件解析，chunks=%s, images=%s, elapsed_ms=%s",
            chunk_count,
            image_count,
            int((time.time() - stage_start) * 1000),
        )
        stage_start = time.time()

        # 向量化（批次一次送出，大幅減少 HTTP 往返）
        embed = get_embedding_provider()
        texts_for_embed = [c["text"][:EMBEDDING_MAX_TEXT_LENGTH] for c in chunks]
        unique_text_to_index: dict[str, int] = {}
        unique_texts: list[str] = []
        chunk_to_unique_index: list[int] = []
        for text in texts_for_embed:
            idx = unique_text_to_index.get(text)
            if idx is None:
                idx = len(unique_texts)
                unique_text_to_index[text] = idx
                unique_texts.append(text)
            chunk_to_unique_index.append(idx)
        logger.info(
            "ingest 向量化開始：total_chunks=%s, unique_texts=%s, batch_size=%s, max_text_len=%s",
            len(texts_for_embed),
            len(unique_texts),
            EMBEDDING_BATCH_SIZE,
            EMBEDDING_MAX_TEXT_LENGTH,
        )
        unique_vectors: list[list[float]] = []

        def _embed_batch_with_retry(batch_texts: list[str], batch_tag: str) -> list[list[float]]:
            last_error: Exception | None = None
            for attempt in range(1, EMBEDDING_BATCH_MAX_RETRIES + 2):
                try:
                    return embed.embed(batch_texts)
                except Exception as e:
                    last_error = e
                    if attempt > EMBEDDING_BATCH_MAX_RETRIES:
                        break
                    backoff_ms = EMBEDDING_BATCH_RETRY_BACKOFF_MS * (2 ** (attempt - 1))
                    logger.warning(
                        "ingest 向量化批次重試：batch=%s, attempt=%s/%s, backoff_ms=%s, error=%s",
                        batch_tag,
                        attempt,
                        EMBEDDING_BATCH_MAX_RETRIES + 1,
                        backoff_ms,
                        str(e),
                    )
                    time.sleep(backoff_ms / 1000.0)

            if len(batch_texts) > EMBEDDING_BATCH_MIN_SIZE:
                split_at = len(batch_texts) // 2
                left = batch_texts[:split_at]
                right = batch_texts[split_at:]
                logger.warning(
                    "ingest 向量化批次拆分重試：batch=%s, original_size=%s, left_size=%s, right_size=%s",
                    batch_tag,
                    len(batch_texts),
                    len(left),
                    len(right),
                )
                return _embed_batch_with_retry(left, f"{batch_tag}-L") + _embed_batch_with_retry(right, f"{batch_tag}-R")

            raise last_error if last_error else RuntimeError("未知的向量化錯誤")

        for i in range(0, len(unique_texts), EMBEDDING_BATCH_SIZE):
            batch_start = time.time()
            batch = unique_texts[i:i + EMBEDDING_BATCH_SIZE]
            batch_no = i // EMBEDDING_BATCH_SIZE + 1
            batch_total = (len(unique_texts) + EMBEDDING_BATCH_SIZE - 1) // EMBEDDING_BATCH_SIZE
            logger.info(
                "ingest 向量化批次開始：batch=%s/%s, size=%s",
                batch_no,
                batch_total,
                len(batch),
            )
            try:
                unique_vectors.extend(_embed_batch_with_retry(batch, f"{batch_no}/{batch_total}"))
            except Exception as e:
                logger.exception(
                    "ingest 向量化批次失敗：batch=%s/%s, size=%s",
                    batch_no,
                    batch_total,
                    len(batch),
                )
                raise HTTPException(
                    status_code=504,
                    detail={
                        "error": "Embedding timeout or connection error",
                        "message": str(e),
                        "batch": f"{batch_no}/{batch_total}",
                    },
                )
            logger.info(
                "ingest 向量化批次完成：batch=%s/%s, elapsed_ms=%s",
                batch_no,
                batch_total,
                int((time.time() - batch_start) * 1000),
            )
        for chunk, unique_idx in zip(chunks, chunk_to_unique_index):
            chunk["embedding"] = unique_vectors[unique_idx]
        logger.info("ingest 階段完成：向量化，elapsed_ms=%s", int((time.time() - stage_start) * 1000))
        stage_start = time.time()

        # 寫入 PostgreSQL
        try:
            # 同一 file_id 重跑時先清舊 chunks，避免累積重複資料
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM document_chunks WHERE file_id = %s", (file_id,))
                conn.commit()
            insert_chunks(chunks)
        except psycopg2.errors.UndefinedTable:
            logger.warning("document_chunks 表不存在，嘗試自動 init_db 重建後再寫入...")
            init_db()
            insert_chunks(chunks)
        logger.info("ingest 階段完成：寫入 chunks，elapsed_ms=%s", int((time.time() - stage_start) * 1000))

        elapsed_ms = int((time.time() - start) * 1000)

        return {
            "file_id": file_id,
            "filename": filename,
            "file_type": file_ext.lstrip("."),
            "confidentiality": confidentiality,
            "status": "completed",
            "chunk_count": chunk_count,
            "image_count": image_count,
            "processing_time_ms": elapsed_ms,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("入庫錯誤：filename=%s", filename)
        raise HTTPException(status_code=500, detail={"error": str(e)})
    finally:
        if temp_path.exists():
            os.remove(temp_path)
        if converted_docx and Path(converted_docx).exists():
            os.remove(converted_docx)
            Path(converted_docx).parent.rmdir()  # 嘗試刪除空目錄
        if converted_pptx and Path(converted_pptx).exists():
            os.remove(converted_pptx)
            Path(converted_pptx).parent.rmdir()  # 嘗試刪除空目錄


async def _ingest_file_content(filename: str, content: bytes, metadata: dict | None = None) -> dict:
    """
    非阻塞包裝：將 CPU/IO 密集入庫工作放到 thread pool，
    避免長任務卡住 FastAPI event loop，讓 search 請求可持續處理。
    """
    return await asyncio.to_thread(_ingest_file_content_sync, filename, content, metadata)


def _download_file_bytes(file_url: str) -> bytes:
    """同步下載檔案 bytes（供 asyncio.to_thread 呼叫）。"""
    with urlopen(file_url, timeout=SQL_BATCH_DOWNLOAD_TIMEOUT_SEC) as response:
        return response.read()


async def _process_sql_batch_row(idx: int, row: tuple, columns: list[str], sem: asyncio.Semaphore) -> tuple[int, dict]:
    """
    處理單一 SQL 批次列（下載 + 入庫），回傳 (index, result) 以便排序。
    使用 semaphore 控制並行數，避免壓垮 embedding/DB 後端。
    """
    async with sem:
        row_map = {str(columns[i]).upper(): row[i] for i in range(min(len(columns), len(row)))}
        file_path = row_map.get("FILE_PATH")
        source_filename = row_map.get("FILE_NAME")
        source_doc_id = row_map.get("DOC_ID")
        source_secret_level = row_map.get("SECRETLEVEL")

        if not file_path:
            return idx, {
                "index": idx,
                "status": "failed",
                "error": "SQL 缺少 file_path",
            }

        normalized_path = str(file_path).replace("\\", "/").lstrip("~").lstrip("/")
        encoded_path = "/".join(quote(part) for part in normalized_path.split("/") if part)
        file_url = f"{SQL_BATCH_FILE_BASE_URL.rstrip('/')}/{encoded_path}"
        metadata = {
            "source": "sql_batch",
            "source_file_path": str(file_path),
            "source_file_url": file_url,
        }
        if source_doc_id:
            metadata["document_id"] = str(source_doc_id)
        if source_secret_level:
            metadata["confidentiality"] = str(source_secret_level)

        try:
            file_bytes = await asyncio.to_thread(_download_file_bytes, file_url)
            filename = (str(source_filename) if source_filename else Path(normalized_path).name) or "unknown"
            ingest_result = await _ingest_file_content(
                filename=filename,
                content=file_bytes,
                metadata=metadata,
            )
            return idx, {
                "index": idx,
                "status": "completed",
                "file_path": file_path,
                "file_url": file_url,
                "file_id": ingest_result.get("file_id"),
                "chunk_count": ingest_result.get("chunk_count", 0),
            }
        except HTTPException as e:
            return idx, {
                "index": idx,
                "status": "failed",
                "file_path": file_path,
                "file_url": file_url,
                "error": e.detail,
            }
        except FileNotFoundError:
            return idx, {
                "index": idx,
                "status": "failed",
                "file_path": file_path,
                "file_url": file_url,
                "error": "檔案不存在",
            }
        except Exception as e:
            return idx, {
                "index": idx,
                "status": "failed",
                "file_path": file_path,
                "file_url": file_url,
                "error": str(e),
            }


async def _process_sql_batch_row_with_retry(idx: int, row: tuple, columns: list[str], sem: asyncio.Semaphore) -> tuple[int, dict]:
    """單列批次處理重試包裝，提升不穩定網路或暫時性錯誤成功率。"""
    last_result: tuple[int, dict] | None = None
    for attempt in range(1, SQL_BATCH_ROW_MAX_RETRIES + 2):
        result = await _process_sql_batch_row(idx, row, columns, sem)
        last_result = result
        if result[1].get("status") == "completed":
            return result
        if attempt <= SQL_BATCH_ROW_MAX_RETRIES:
            backoff_ms = SQL_BATCH_ROW_RETRY_BACKOFF_MS * (2 ** (attempt - 1))
            await asyncio.sleep(backoff_ms / 1000.0)
    return last_result if last_result else (idx, {"index": idx, "status": "failed", "error": "未知錯誤"})


def _cleanup_expired_jobs() -> None:
    """清除過期工作，避免 in-memory job list 持續成長。"""
    now = time.time()
    expired = []
    for job_id, job in SQL_BATCH_JOBS.items():
        if now - float(job.get("updated_at_ts", now)) > BATCH_JOB_TTL_SECONDS:
            expired.append(job_id)
    for job_id in expired:
        SQL_BATCH_JOBS.pop(job_id, None)


def _job_summary(job: dict) -> dict:
    processed = int(job.get("processed_count", 0))
    total = int(job.get("total_count", 0))
    progress = round((processed / total) * 100, 2) if total > 0 else 0.0
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "source_count": job.get("source_count", 0),
        "processed_count": processed,
        "total_count": total,
        "success_count": job.get("success_count", 0),
        "failed_count": job.get("failed_count", 0),
        "progress_percent": progress,
        "requested_num": job.get("requested_num"),
        "normal_concurrency": job.get("normal_concurrency"),
        "search_protect_concurrency": job.get("search_protect_concurrency"),
        "chunk_size": job.get("chunk_size"),
        "error": job.get("error"),
    }


async def _run_sql_batch_job(job_id: str, req: SqlBatchIngestRequest) -> None:
    """背景執行 SQL 批次入庫，並持續更新 job 進度。"""
    job = SQL_BATCH_JOBS[job_id]
    try:
        rows, columns = await asyncio.to_thread(_fetch_sql_batch_source_rows)
        total_source = len(rows)
        selected_rows = rows if req.num == -1 else rows[:req.num]

        job["status"] = "running"
        job["source_count"] = total_source
        job["total_count"] = len(selected_rows)
        job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        job["updated_at_ts"] = time.time()

        results: list[dict] = []
        for chunk_start in range(0, len(selected_rows), SQL_BATCH_CHUNK_SIZE):
            row_chunk = selected_rows[chunk_start:chunk_start + SQL_BATCH_CHUNK_SIZE]
            effective_concurrency = SQL_BATCH_CONCURRENCY
            if SEARCH_PROTECT_ENABLED and ACTIVE_SEARCH_REQUESTS >= SEARCH_PROTECT_THRESHOLD:
                effective_concurrency = min(SQL_BATCH_CONCURRENCY, SEARCH_PROTECT_INGEST_CONCURRENCY)

            sem = asyncio.Semaphore(effective_concurrency)
            tasks = [
                _process_sql_batch_row_with_retry(chunk_start + idx + 1, row, columns, sem)
                for idx, row in enumerate(row_chunk)
            ]
            chunk_results = await asyncio.gather(*tasks)
            chunk_results.sort(key=lambda x: x[0])
            results.extend(item for _, item in chunk_results)

            processed = len(results)
            success = sum(1 for r in results if r["status"] == "completed")
            failed = processed - success
            job["processed_count"] = processed
            job["success_count"] = success
            job["failed_count"] = failed
            job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job["updated_at_ts"] = time.time()

        job["status"] = "completed"
        job["results"] = results
        job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        job["updated_at_ts"] = time.time()
    except Exception as e:
        logger.exception("SQL 批次入庫背景任務失敗：job_id=%s", job_id)
        job["status"] = "failed"
        job["error"] = str(e)
        job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        job["updated_at_ts"] = time.time()


# ======================== API 端點 ========================

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    metadata: str | None = Form(default=None),
):
    """上傳檔案入庫（content_hash 去重）"""
    meta = {}
    if metadata:
        try:
            meta = json.loads(metadata)
        except Exception:
            pass

    content = await file.read()
    result = await _ingest_file_content(
        filename=file.filename or "unknown",
        content=content,
        metadata=meta,
    )
    return JSONResponse(result)


@app.post("/ingest/sql-batch")
async def ingest_sql_batch(req: SqlBatchIngestRequest):
    """建立 SQL 批次入庫背景任務，立即回傳 job_id。"""
    if req.apikey != SQL_BATCH_API_KEY:
        raise HTTPException(status_code=403, detail={"error": "Invalid apikey"})
    if req.num == 0 or req.num < -1:
        raise HTTPException(status_code=400, detail={"error": "num 必須為正整數或 -1"})

    _cleanup_expired_jobs()
    job_id = str(uuid.uuid4())
    now_text = time.strftime("%Y-%m-%d %H:%M:%S")
    SQL_BATCH_JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "created_at": now_text,
        "updated_at": now_text,
        "updated_at_ts": time.time(),
        "requested_num": req.num,
        "source_count": 0,
        "processed_count": 0,
        "total_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "normal_concurrency": SQL_BATCH_CONCURRENCY,
        "search_protect_concurrency": SEARCH_PROTECT_INGEST_CONCURRENCY,
        "chunk_size": SQL_BATCH_CHUNK_SIZE,
        "results": [],
        "error": None,
    }
    asyncio.create_task(_run_sql_batch_job(job_id, req))

    return JSONResponse({
        "status": "accepted",
        "job_id": job_id,
        "message": "SQL 批次入庫已加入背景任務，請用 job_id 查詢進度",
    })


@app.get("/ingest/sql-batch/{job_id}")
async def ingest_sql_batch_status(job_id: str):
    """查詢 SQL 批次入庫背景任務狀態與進度。"""
    _cleanup_expired_jobs()
    job = SQL_BATCH_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error": "找不到 job_id，可能已過期"})
    return JSONResponse(_job_summary(job))


@app.get("/ingest/sql-batch/{job_id}/results")
async def ingest_sql_batch_results(job_id: str):
    """查詢 SQL 批次入庫背景任務結果明細。"""
    _cleanup_expired_jobs()
    job = SQL_BATCH_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error": "找不到 job_id，可能已過期"})
    return JSONResponse({
        **_job_summary(job),
        "results": job.get("results", []),
    })


@app.post("/search")
async def search_endpoint(req: SearchRequest):
    """搜尋文件（可指定 user_id 做權限過濾）"""
    global ACTIVE_SEARCH_REQUESTS
    ACTIVE_SEARCH_REQUESTS += 1
    try:
        embed = get_embedding_provider()
        vectors = embed.embed([req.query])
        query_vector = vectors[0]

        results = pg_search(
            query_vector=query_vector,
            top_k=req.top_k,
            user_id=req.user_id,
            file_types=req.file_types,
            confidentiality=req.confidentiality,
        )

        return JSONResponse({
            "query": req.query,
            "total": len(results),
            "results": results,
        })

    except Exception as e:
        logger.exception("搜尋錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})
    finally:
        ACTIVE_SEARCH_REQUESTS = max(0, ACTIVE_SEARCH_REQUESTS - 1)


@app.post("/permissions/sync")
async def sync_permissions(req: PermissionSyncRequest):
    """
    同步使用者權限
    原系統呼叫此端點更新每位使用者的文件訪問權限
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 刪除該 user 的舊權限
                cur.execute(
                    "DELETE FROM document_permissions WHERE user_id = %s",
                    (req.user_id,)
                )

                # 寫入新權限
                records = [
                    (req.user_id, doc["document_id"], doc.get("access_level", "read"))
                    for doc in req.documents
                ]
                if records:
                    execute_values = __import__("psycopg2.extras", fromlist=["execute_values"]).execute_values
                    execute_values(
                        cur,
                        "INSERT INTO document_permissions (user_id, document_id, access_level) VALUES %s",
                        records,
                        template="(%s, %s, %s)",
                    )
                conn.commit()

        return JSONResponse({
            "status": "ok",
            "user_id": req.user_id,
            "document_count": len(req.documents),
        })

    except Exception as e:
        logger.exception("權限同步錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.get("/permissions/{user_id}")
async def get_user_permissions(user_id: str):
    """查詢使用者的文件權限"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT dp.document_id, dp.access_level, dp.granted_at,
                           d.filename, d.confidentiality
                    FROM document_permissions dp
                    JOIN documents d ON dp.document_id = d.file_id
                    WHERE dp.user_id = %s
                """, (user_id,))
                rows = cur.fetchall()

        return JSONResponse({
            "user_id": user_id,
            "documents": [
                {
                    "document_id": r[0],
                    "access_level": r[1],
                    "granted_at": r[2],
                    "filename": r[3],
                    "confidentiality": r[4],
                }
                for r in rows
            ],
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/export/ppt")
async def export_ppt(req: ExportPptRequest):
    """匯出 PPT"""
    try:
        chunks = get_chunks_by_ids(req.result_ids)
        if not chunks:
            raise HTTPException(status_code=404, detail={"error": "找不到指定的 chunks"})

        ppt_bytes = generate_ppt(
            chunks=chunks,
            output_name=req.output_name,
            include_images=req.include_images,
        )

        output_filename = f"{req.output_name}.pptx"
        return StreamingResponse(
            io.BytesIO(ppt_bytes),
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{output_filename}"},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("匯出錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/scrape")
async def scrape_endpoint(req: ScrapeRequest):
    """
    爬取公開網頁內容。

    - url：目標網址
    - selector：CSS 選擇器，只取符合的元素（選填）
    - extract_links：是否回傳連結清單（預設 False）
    - timeout：請求逾時，預設 30 秒
    """
    try:
        result = await do_scrape(
            url=req.url,
            selector=req.selector,
            extract_links_flag=req.extract_links,
            timeout=req.timeout,
        )
        return JSONResponse(result)
    except Exception as e:
        logger.exception("爬蟲錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/generate/ppt")
async def generate_ppt_from_outline_endpoint(req: GeneratePptFromOutlineRequest):
    """
    根據大綱結構生成 PPT。

    outline 格式：
    {
        "title": "報告標題",
        "subtitle": "副標題（選填）",
        "author": "作者（選填）",
        "slides": [
            {"title": "第一頁標題", "bullets": ["項目一", "項目二"], "notes": "備註（選填）"},
            ...
        ]
    }
    """
    try:
        ppt_bytes = generate_ppt_from_outline(
            outline=req.outline,
            output_name=req.output_name,
        )

        output_filename = f"{req.output_name}.pptx"
        return StreamingResponse(
            io.BytesIO(ppt_bytes),
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{output_filename}"},
        )
    except Exception as e:
        logger.exception("PPT 生成錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/generate/pdf")
async def generate_pdf_from_outline_endpoint(req: GeneratePdfFromOutlineRequest):
    """
    根據大綱結構生成 PDF 報告。

    outline 格式：
    {
        "title": "報告標題",
        "subtitle": "副標題（選填）",
        "author": "作者（選填）",
        "date": "2025/01/01（選填，預設今天）",
        "sections": [
            {
                "heading": "章節標題",
                "content": "內文" or ["項目一", "項目二"],
                "table": [["標題", "內容"], ...]（選填）
            }
        ]
    }
    """
    try:
        pdf_bytes = generate_pdf_from_outline(
            outline=req.outline,
            output_name=req.output_name,
        )

        output_filename = f"{req.output_name}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{output_filename}"},
        )
    except Exception as e:
        logger.exception("PDF 生成錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/export/pdf")
async def export_pdf_from_chunks_endpoint(req: GeneratePdfFromChunksRequest):
    """
    將向量搜尋結果（chunks）匯出為 PDF 文件。
    """
    try:
        chunks = get_chunks_by_ids(req.result_ids)
        if not chunks:
            raise HTTPException(status_code=404, detail={"error": "找不到指定的 chunks"})

        pdf_bytes = generate_pdf_from_chunks(
            chunks=chunks,
            output_name=req.output_name,
        )

        output_filename = f"{req.output_name}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{output_filename}"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("PDF 匯出錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/generate/excel")
async def generate_excel_endpoint(req: GenerateExcelRequest):
    """
    根據資料結構生成 Excel 報表。

    sheets 格式：
    [{
        "name": "工作表名稱",
        "headers": ["欄位A", "欄位B"],
        "data": [
            ["值1", "值2"],
            ["值3", "值4"]
        ]
    }]

    headers 可為 dict 設定寬度與對齊：
    ["欄位A", {"label": "欄位B", "width": 20, "align": "center"}]

    data 每列可為 list 或 dict（以 headers 為 key）。
    """
    try:
        xlsx_bytes = generate_excel_from_data(
            sheets=req.sheets,
            output_name=req.output_name,
            title=req.title,
        )

        output_filename = f"{req.output_name}.xlsx"
        return StreamingResponse(
            io.BytesIO(xlsx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{output_filename}"},
        )
    except Exception as e:
        logger.exception("Excel 生成錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.post("/generate/word")
async def generate_word_endpoint(req: GenerateWordRequest):
    """
    根據大綱結構生成 Word 文件。

    outline 格式：
    {
        "title": "文件標題",
        "subtitle": "副標題（選填）",
        "author": "作者（選填）",
        "date": "2025/01/01（選填，預設今天）",
        "sections": [
            {
                "heading": "章節標題",
                "content": "內文" or ["項目一", "項目二"],
                "table": [["標題1", "標題2"], ["內容1", "內容2"], ...]（選填）
            }
        ]
    }
    """
    try:
        docx_bytes = generate_word_from_outline(
            outline=req.outline,
            output_name=req.output_name,
        )

        output_filename = f"{req.output_name}.docx"
        return StreamingResponse(
            io.BytesIO(docx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{output_filename}"},
        )
    except Exception as e:
        logger.exception("Word 生成錯誤")
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.get("/collections/stats")
async def collections_stats():
    """統計資訊"""
    try:
        stats = get_stats()
        return JSONResponse(stats)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": str(e)})


@app.delete("/collection/{file_id}")
async def delete_file_chunks(file_id: str):
    """刪除檔案（會 Cascade 刪除 chunks 和權限）"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM documents WHERE file_id = %s", (file_id,))
                conn.commit()
        return JSONResponse({"status": "deleted", "file_id": file_id})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": str(e)})


# ======================== 啟動 ========================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="info")
