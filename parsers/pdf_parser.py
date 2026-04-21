import sys
sys.path.append(".")

import uuid
import fitz  # PyMuPDF
from pathlib import Path
from config import IMAGE_DIR
from datetime import datetime, timezone


def chunk_text(text: str, max_len: int = 8000, overlap: int = 200) -> list[str]:
    """
    將長文字切成多個 overlapping sub-chunks，避免超過 embedding 模型上限。
    保留 overlap 避免斷句遺漏上下文。
    """
    if len(text) <= max_len:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_len
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def parse_pdf(file_path: str, file_id: str, metadata: dict | None = None) -> tuple[list[dict], list[str]]:
    """
    解析 PDF 檔案，回傳 (chunks, image_paths)
    - 文字：每 chunk 最多 800 字，超長段落用 chunk_text 切成 sub-chunks
    - 圖片：每張圖建立獨立的 image_chunk，回傳於 image_paths
    """
    chunks = []
    image_paths = []
    metadata = metadata or {}

    doc = fitz.open(file_path)
    base_name = Path(file_path).stem

    for page_num, page in enumerate(doc, start=1):
        # --- 文字萃取 ---
        text = page.get_text("text").strip()

        # 先把前一頁累積的 current_text flush 完（跨頁長段落保護）
        current_text = ""
        chunk_index = 0

        if text:
            paragraphs = text.split("\n")
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue

                # 超長段落（>8000字）直接用 chunk_text 切成 sub-chunks
                if len(para) > 8000:
                    if current_text.strip():
                        chunks.append(_make_chunk(
                            file_id=file_id, source_file=Path(file_path).name,
                            file_type="pdf", page=page_num, chunk_index=chunk_index,
                            text=current_text.strip(), image_paths=[], metadata=metadata,
                        ))
                        chunk_index += 1
                        current_text = ""
                    for sub_text in chunk_text(para, max_len=8000, overlap=200):
                        chunks.append(_make_chunk(
                            file_id=file_id, source_file=Path(file_path).name,
                            file_type="pdf", page=page_num, chunk_index=chunk_index,
                            text=sub_text, image_paths=[], metadata=metadata,
                        ))
                        chunk_index += 1
                    continue

                if len(current_text) + len(para) <= 800:
                    current_text += para + "\n"
                else:
                    chunks.append(_make_chunk(
                        file_id=file_id, source_file=Path(file_path).name,
                        file_type="pdf", page=page_num, chunk_index=chunk_index,
                        text=current_text.strip(), image_paths=[], metadata=metadata,
                    ))
                    chunk_index += 1
                    current_text = para + "\n"

            # 該頁最後一塊
            if current_text.strip():
                chunks.append(_make_chunk(
                    file_id=file_id, source_file=Path(file_path).name,
                    file_type="pdf", page=page_num, chunk_index=chunk_index,
                    text=current_text.strip(), image_paths=[], metadata=metadata,
                ))
                chunk_index += 1

        # --- 圖片萃取（每張圖建立獨立的 image_chunk）---
        image_list = page.get_images(full=True)
        page_img_dir = Path(IMAGE_DIR) / file_id
        page_img_dir.mkdir(parents=True, exist_ok=True)

        for img_idx, img in enumerate(image_list):
            xref = img[0]
            pix = fitz.Pixmap(doc, xref)
            img_name = f"{base_name}_p{page_num}_{img_idx+1}.png"
            img_path = str(page_img_dir / img_name)

            if pix.n - 1 < 5:  # RGB or Gray
                pix.save(img_path)
            else:  # CMYK -> RGB
                pix = fitz.Pixmap(fitz.csRGB, pix)
                pix.save(img_path)

            image_paths.append(img_path)
            # 每張圖建立一個獨立的 image_chunk
            chunks.append(_make_chunk(
                file_id=file_id,
                source_file=Path(file_path).name,
                file_type="pdf_image",  # 區分文字 chunk 與圖片 chunk
                page=page_num,
                chunk_index=chunk_index,
                text=f"[圖片：{img_name}]",
                image_paths=[img_path],
                metadata=metadata,
            ))
            chunk_index += 1

    doc.close()
    return chunks, image_paths


def _make_chunk(file_id: str, source_file: str, file_type: str, page: int, chunk_index: int, text: str, image_paths: list[str], metadata: dict) -> dict:
    import json
    return {
        "chunk_id": str(uuid.uuid4()),
        "file_id": file_id,
        "source_file": source_file,
        "file_type": file_type,
        "page": page,
        "chunk_index": chunk_index,
        "text": text,
        "image_paths": image_paths,
        "metadata_json": json.dumps(metadata),
        "embedding": None,  # 後續填入
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
