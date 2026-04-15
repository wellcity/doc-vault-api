"""
PDF 報告生成器
支援兩種模式：從大綱生成 / 從文件 chunks 摘要生成
"""
import sys
sys.path.append(".")

import io
import datetime
from typing import Literal

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor, white, black
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT


# ── 配色 ────────────────────────────────────────────────────────────────────

COLOR_PRIMARY = HexColor("#1F497D")    # 深藍（標題）
COLOR_ACCENT = HexColor("#2E86AB")     # 亮藍（裝飾線）
COLOR_TEXT = HexColor("#262626")       # 深灰（內文）
COLOR_MUTED = HexColor("#888888")      # 淺灰（次要文字）
COLOR_LIGHT_BG = HexColor("#F5F7FA")   # 極淺灰（表格背景）


# ── 樣式工廠 ────────────────────────────────────────────────────────────────

def build_styles():
    base = getSampleStyleSheet()

    styles = {}

    styles["title"] = ParagraphStyle(
        "title",
        fontSize=28,
        leading=34,
        textColor=white,
        fontName="Helvetica-Bold",
        alignment=TA_LEFT,
        spaceAfter=6,
    )

    styles["subtitle"] = ParagraphStyle(
        "subtitle",
        fontSize=14,
        leading=18,
        textColor=HexColor("#CCDDFF"),
        fontName="Helvetica",
        alignment=TA_LEFT,
        spaceAfter=4,
    )

    styles["meta"] = ParagraphStyle(
        "meta",
        fontSize=10,
        leading=14,
        textColor=HexColor("#AAAAAA"),
        fontName="Helvetica",
        alignment=TA_RIGHT,
    )

    styles["h1"] = ParagraphStyle(
        "h1",
        fontSize=18,
        leading=24,
        textColor=COLOR_PRIMARY,
        fontName="Helvetica-Bold",
        spaceBefore=20,
        spaceAfter=8,
    )

    styles["h2"] = ParagraphStyle(
        "h2",
        fontSize=14,
        leading=18,
        textColor=COLOR_PRIMARY,
        fontName="Helvetica-Bold",
        spaceBefore=14,
        spaceAfter=6,
    )

    styles["body"] = ParagraphStyle(
        "body",
        fontSize=10,
        leading=15,
        textColor=COLOR_TEXT,
        fontName="Helvetica",
        spaceAfter=6,
        alignment=TA_LEFT,
    )

    styles["bullet"] = ParagraphStyle(
        "bullet",
        fontSize=10,
        leading=15,
        textColor=COLOR_TEXT,
        fontName="Helvetica",
        leftIndent=16,
        spaceAfter=4,
        bulletIndent=4,
    )

    styles["footer"] = ParagraphStyle(
        "footer",
        fontSize=8,
        leading=10,
        textColor=COLOR_MUTED,
        fontName="Helvetica",
        alignment=TA_CENTER,
    )

    styles["table_header"] = ParagraphStyle(
        "table_header",
        fontSize=9,
        leading=12,
        textColor=white,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
    )

    styles["table_cell"] = ParagraphStyle(
        "table_cell",
        fontSize=9,
        leading=12,
        textColor=COLOR_TEXT,
        fontName="Helvetica",
        alignment=TA_LEFT,
    )

    return styles


# ── 頁面背景（封面） ────────────────────────────────────────────────────────

def _cover_page(canvas, doc):
    canvas.saveState()
    # 深藍頂部橫幅
    canvas.setFillColor(COLOR_PRIMARY)
    canvas.rect(0, A4[1] - 7.5 * cm, A4[0], 7.5 * cm, fill=1, stroke=0)
    # 底部分隔線
    canvas.setStrokeColor(COLOR_ACCENT)
    canvas.setLineWidth(2)
    canvas.line(2 * cm, 2 * cm, A4[0] - 2 * cm, 2 * cm)
    # 頁碼
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(COLOR_MUTED)
    canvas.drawCentredString(A4[0] / 2, 1.2 * cm, f"- {doc.page} -")
    canvas.restoreState()


def _normal_page(canvas, doc):
    canvas.saveState()
    # 頂部分隔線
    canvas.setStrokeColor(COLOR_ACCENT)
    canvas.setLineWidth(1.5)
    canvas.line(2 * cm, A4[1] - 1.5 * cm, A4[0] - 2 * cm, A4[1] - 1.5 * cm)
    # 頁碼
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(COLOR_MUTED)
    canvas.drawCentredString(A4[0] / 2, 1.2 * cm, f"- {doc.page} -")
    canvas.restoreState()


# ── 主函式 ────────────────────────────────────────────────────────────────

def generate_pdf_from_outline(outline: dict, output_name: str = "report") -> bytes:
    """
    根據大綱結構生成 PDF 報告。

    Args:
        outline: {
            "title": str,
            "subtitle": str (optional),
            "author": str (optional),
            "date": str (optional, 預設今天),
            "sections": [
                {
                    "heading": str,
                    "content": str | list[str],
                    "table": list[list[str]] (optional)
                }
            ]
        }
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
        topMargin=2.5 * cm,
        bottomMargin=2.5 * cm,
    )

    styles = build_styles()
    story = []

    # ── 封面 ──
    story.append(Spacer(1, 5.5 * cm))
    story.append(Paragraph(outline.get("title", "報告"), styles["title"]))

    subtitle = outline.get("subtitle")
    if subtitle:
        story.append(Paragraph(subtitle, styles["subtitle"]))

    story.append(Spacer(1, 0.5 * cm))

    meta_parts = []
    if outline.get("author"):
        meta_parts.append(outline["author"])
    date_str = outline.get("date") or datetime.date.today().strftime("%Y/%m/%d")
    meta_parts.append(date_str)
    story.append(Paragraph("  |  ".join(meta_parts), styles["meta"]))

    story.append(Spacer(1, 1 * cm))
    story.append(HRFlowable(width="100%", thickness=2, color=COLOR_ACCENT))
    story.append(PageBreak())

    # ── 內容區 ──
    sections = outline.get("sections", [])

    for sec in sections:
        heading = sec.get("heading", "")
        content = sec.get("content", "")
        table_data = sec.get("table")

        # 標題
        if heading:
            story.append(Paragraph(heading, styles["h1"]))
            story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_ACCENT, spaceAfter=8))

        # 內文（str 或 list）
        if content:
            if isinstance(content, str):
                for para in content.split("\n\n"):
                    if para.strip():
                        story.append(Paragraph(para.strip(), styles["body"]))
            elif isinstance(content, list):
                for item in content:
                    story.append(Paragraph(f"• {item}", styles["bullet"]))

        # 表格
        if table_data:
            story.append(Spacer(1, 8))
            tbl = Table(table_data, colWidths=[4 * cm, 8 * cm])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), COLOR_PRIMARY),
                ("TEXTCOLOR", (0, 0), (-1, 0), white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#DDDDDD")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, COLOR_LIGHT_BG]),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]))
            story.append(tbl)

        story.append(Spacer(1, 0.5 * cm))

    doc.build(story, onFirstPage=_cover_page, onLaterPages=_normal_page)
    buffer.seek(0)
    return buffer.read()


def generate_pdf_from_chunks(chunks: list[dict], output_name: str = "document") -> bytes:
    """
    將文件 chunks 摘要生成 PDF。

    Args:
        chunks: [{
            "text": str,
            "source_file": str,
            "page": int,
            "file_type": str
        }]
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
        topMargin=2.5 * cm,
        bottomMargin=2.5 * cm,
    )

    styles = build_styles()
    story = []

    # 封面
    story.append(Spacer(1, 5.5 * cm))
    story.append(Paragraph("文件摘要報告", styles["title"]))
    story.append(Paragraph(f"共 {len(chunks)} 個段落", styles["subtitle"]))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(datetime.date.today().strftime("%Y/%m/%d"), styles["meta"]))
    story.append(Spacer(1, 1 * cm))
    story.append(HRFlowable(width="100%", thickness=2, color=COLOR_ACCENT))
    story.append(PageBreak())

    for idx, chunk in enumerate(chunks, 1):
        text = chunk.get("text", "")
        source = chunk.get("source_file", "")
        page = chunk.get("page", 0)
        ftype = chunk.get("file_type", "").upper()

        # 段落抬頭
        header_text = f"[{ftype}] {source} — 第 {page} 頁" if source else f"段落 {idx}"
        story.append(Paragraph(header_text, styles["h2"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_ACCENT, spaceAfter=6))

        # 內容
        for line in text.split("\n"):
            if line.strip():
                story.append(Paragraph(line.strip(), styles["body"]))

        story.append(Spacer(1, 0.4 * cm))

        if idx % 3 == 0:  # 每3段分頁
            story.append(PageBreak())

    doc.build(story, onFirstPage=_cover_page, onLaterPages=_normal_page)
    buffer.seek(0)
    return buffer.read()
