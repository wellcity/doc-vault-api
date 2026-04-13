import sys
sys.path.append(".")

import uuid
import fitz  # PyMuPDF
from pathlib import Path
from config import IMAGE_DIR
from datetime import datetime, timezone


def parse_pdf(file_path: str, file_id: str, metadata: dict | None = None) -> tuple[list[dict], list[str]]:
    """
    解析 PDF 檔案，回傳 (chunks, image_paths)
    """
    chunks = []
    image_paths = []
    metadata = metadata or {}

    doc = fitz.open(file_path)
    base_name = Path(file_path).stem

    for page_num, page in enumerate(doc, start=1):
        # 萃取文字
        text = page.get_text("text")
        if not text.strip():
            continue

        # 切割段落（每 chunk 最多 800 字）
        paragraphs = text.split("\n")
        current_text = ""
        chunk_index = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current_text) + len(para) <= 800:
                current_text += para + "\n"
            else:
                if current_text.strip():
                    chunk = _make_chunk(
                        file_id=file_id,
                        source_file=Path(file_path).name,
                        file_type="pdf",
                        page=page_num,
                        chunk_index=chunk_index,
                        text=current_text.strip(),
                        image_paths=[],  # PDF 圖片先不處理
                        metadata=metadata,
                    )
                    chunks.append(chunk)
                    chunk_index += 1

                current_text = para + "\n"

        # 最後一塊
        if current_text.strip():
            chunk = _make_chunk(
                file_id=file_id,
                source_file=Path(file_path).name,
                file_type="pdf",
                page=page_num,
                chunk_index=chunk_index,
                text=current_text.strip(),
                image_paths=[],
                metadata=metadata,
            )
            chunks.append(chunk)

        # 萃取圖片
        image_list = page.get_images(full=True)
        page_img_dir = Path(IMAGE_DIR) / file_id
        page_img_dir.mkdir(parents=True, exist_ok=True)

        page_image_paths = []
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
            page_image_paths.append(img_path)

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
