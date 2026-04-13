import sys
sys.path.append(".")

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pathlib import Path
import io
import base64
from config import OUTPUT_DIR


def generate_ppt(chunks: list[dict], output_name: str = "report", include_images: bool = True) -> bytes:
    """
    將 chunks 生成 PPT，回傳 BytesIO
    """
    prs = Presentation()
    prs.slide_width = Inches(13.33)
    prs.slide_height = Inches(7.5)

    blank_layout = prs.slide_layouts[6]  # 空白版面

    for chunk in chunks:
        slide = prs.slides.add_slide(blank_layout)

        # === 標題列 ===
        title_left = Inches(0.3)
        title_top = Inches(0.2)
        title_width = Inches(12.73)
        title_height = Inches(0.6)

        title_box = slide.shapes.add_textbox(title_left, title_top, title_width, title_height)
        tf = title_box.text_frame
        tf.word_wrap = False
        p = tf.paragraphs[0]
        p.text = f"[{chunk.get('file_type', '').upper()}] {chunk.get('source_file', '')} — 第 {chunk.get('page', 0)} 頁"
        p.font.size = Pt(16)
        p.font.bold = True
        p.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)

        # === 文字內容區 ===
        text_left = Inches(0.3)
        text_top = Inches(1.0)
        text_width = Inches(7.0)
        text_height = Inches(6.0)

        text_box = slide.shapes.add_textbox(text_left, text_top, text_width, text_height)
        tf = text_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = chunk.get("text", "")
        p.font.size = Pt(12)

        # === 圖片區 ===
        if include_images:
            image_paths = chunk.get("image_paths", [])
            if image_paths:
                img_start_x = Inches(7.5)
                img_start_y = Inches(1.0)
                max_img_width = Inches(5.5)
                max_img_height = Inches(6.0)

                for idx, img_path in enumerate(image_paths[:4]):  # 最多4張
                    try:
                        path = Path(img_path)
                        if not path.exists():
                            continue

                        # Grid 排列
                        row = idx // 2
                        col = idx % 2
                        x = img_start_x + Inches(col * 2.7)
                        y = img_start_y + Inches(row * 3.0)

                        slide.shapes.add_picture(str(path), x, y, width=max_img_width / 2, height=max_img_height / 2)
                    except Exception as e:
                        print(f"Warning: cannot add image {img_path}: {e}")

    # 儲存到 BytesIO
    output = io.BytesIO()
    prs.save(output)
    output.seek(0)
    return output.read()
