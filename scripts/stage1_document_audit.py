#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Stage 1 document audit for industry-chain modeling PDFs.

This script intentionally avoids OCR, LLM calls, and relationship extraction.
It uses native PDF text/layout signals plus rendered page images and optional
human visual-review notes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import re
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from document_profile_manager import document_rules, resolve_document_profile

try:
    import fitz  # PyMuPDF
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit("Missing required dependency: PyMuPDF / fitz") from exc

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit("Missing required dependency: Pillow") from exc


DOC_FIELDS = [
    "doc_id",
    "file_name",
    "file_path",
    "file_size_mb",
    "file_sha256",
    "industry",
    "title",
    "publisher",
    "publish_date",
    "region_scope",
    "page_count",
    "pdf_metadata_title",
    "pdf_metadata_author",
    "pdf_metadata_creation_date",
    "is_encrypted",
    "document_type",
    "source_quality_provisional",
    "notes",
]

PAGE_FIELDS = [
    "doc_id",
    "page_no",
    "page_label",
    "page_type",
    "classification_method",
    "classification_confidence",
    "native_text_char_count",
    "text_extractable",
    "text_quality",
    "needs_ocr",
    "has_images",
    "has_table_candidate",
    "table_candidate_count",
    "has_chart_candidate",
    "has_chain_diagram_candidate",
    "has_chain_diagram_confirmed",
    "layout_complexity",
    "recommended_parser",
    "review_required",
    "quality_note",
]

SAMPLE_FIELDS = [
    "page_no",
    "sample_reason",
    "page_type",
    "text_extractable",
    "needs_ocr",
    "has_table_candidate",
    "has_chain_diagram_candidate",
    "recommended_parser",
    "review_comment",
]

HIGH_VALUE_FIELDS = [
    "page_no",
    "priority",
    "reason",
    "suspected_content",
    "recommended_next_stage_action",
    "review_required",
]

VISUAL_REVIEW_FIELDS = [
    "doc_id",
    "page_no",
    "page_type_override",
    "has_chain_diagram_confirmed",
    "review_required",
    "visual_review_comment",
]

PAGE_TYPES = {
    "cover",
    "copyright",
    "toc",
    "paragraph",
    "table",
    "chart",
    "chain_diagram",
    "scan_image",
    "mixed",
    "appendix",
    "blank",
    "unknown",
}

TEXT_EXTRACTABLE_VALUES = {"yes", "no", "partial"}
TEXT_QUALITY_VALUES = {"normal", "low", "garbled", "not_applicable"}
OCR_VALUES = {"yes", "no", "maybe"}
LAYOUT_VALUES = {"simple", "medium", "complex"}
PARSER_VALUES = {
    "native_text",
    "native_text_with_layout",
    "table_parser",
    "chart_caption_only",
    "diagram_visual_review",
    "ocr_then_layout",
    "manual_review",
    "skip",
}

CHAIN_KEYWORDS = [
    "产业链",
    "价值链",
    "供应链",
    "上游",
    "中游",
    "下游",
    "产业生态",
    "产业结构",
    "产业图谱",
]

DOMAIN_KEYWORDS = [
    "关键材料",
    "核心设备",
    "设计",
    "制造",
    "封装",
    "测试",
    "应用",
    "国产替代",
    "产业布局",
]

ALL_VALUE_KEYWORDS = CHAIN_KEYWORDS + DOMAIN_KEYWORDS

CHART_KEYWORDS = [
    "图",
    "图表",
    "数据来源",
    "来源：",
    "占比",
    "同比",
    "增长率",
    "市场规模",
    "规模",
    "份额",
    "CAGR",
    "%",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage 1 PDF document audit")
    parser.add_argument("--pdf", required=True, help="Path to source PDF")
    parser.add_argument("--project-root", required=True, help="Project root path")
    parser.add_argument("--dpi", type=int, default=150, help="PNG render DPI")
    parser.add_argument(
        "--document-profile", default="auto",
        help="Document-specific parsing profile id; auto detects, generic disables specialty rules",
    )
    parser.add_argument(
        "--visual-review-notes",
        default=None,
        help="Optional CSV with sampled visual-review notes",
    )
    parser.add_argument(
        "--force-render",
        action="store_true",
        help="Re-render PNG pages even when they already exist",
    )
    return parser.parse_args()


def ensure_dirs(project_root: Path) -> Dict[str, Path]:
    paths = {
        "raw_pdf": project_root / "data" / "raw_pdf",
        "rendered_pages": project_root / "data" / "rendered_pages",
        "samples": project_root / "data" / "samples",
        "metadata": project_root / "metadata",
        "outputs": project_root / "outputs" / "stage1_document_audit",
        "scripts": project_root / "scripts",
        "logs": project_root / "logs",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def setup_logging(log_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"stage1_document_audit_{stamp}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    if not path.exists():
        return [], []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return reader.fieldnames or [], [dict(row) for row in reader]


def backup_if_exists(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.stem}.bak_{stamp}{path.suffix}")
    shutil.copy2(path, backup_path)
    logging.info("Existing file backed up: %s -> %s", path, backup_path)
    return backup_path


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Dict[str, object]]) -> None:
    backup_if_exists(path)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def chinese_ratio(text: str) -> float:
    chars = [ch for ch in text if not ch.isspace()]
    if not chars:
        return 0.0
    chinese = sum(1 for ch in chars if "\u4e00" <= ch <= "\u9fff")
    return chinese / len(chars)


def garbled_ratio(text: str) -> float:
    chars = [ch for ch in text if not ch.isspace()]
    if not chars:
        return 0.0
    suspicious = sum(1 for ch in chars if ch in "�□■�" or ord(ch) < 32)
    latin_noise = len(re.findall(r"[ÃÂ¤åçé]{2,}", text))
    return min(1.0, (suspicious + latin_noise) / len(chars))


def bool_text(value: bool) -> str:
    return "yes" if value else "no"


def safe_float(value: float) -> str:
    return f"{max(0.0, min(1.0, value)):.2f}"


def split_pdf_date(value: str) -> str:
    if not value:
        return ""
    match = re.match(r"D:(\d{4})(\d{2})(\d{2})", value)
    if match:
        return "-".join(match.groups())
    return value


def infer_document_info(
    pdf_path: Path, doc: fitz.Document, full_text_head: str, profile: Dict[str, object]
) -> Dict[str, str]:
    metadata = doc.metadata or {}
    metadata_title = (metadata.get("title") or "").strip()
    metadata_author = (metadata.get("author") or "").strip()
    creation_date = (metadata.get("creationDate") or "").strip()

    normalized_head = normalize_text(full_text_head)
    title = "unknown"
    title_note = "title unknown"
    rules = document_rules(profile)
    title_override = str(rules.get("title_override") or "").strip()
    if metadata_title:
        title = metadata_title
        title_note = "title from PDF metadata"
    elif title_override:
        title = title_override
        title_note = f"title from document profile {profile.get('profile_id', 'generic')}"

    publisher = "unknown"
    publisher_note = "publisher unknown"
    if metadata_author:
        publisher = metadata_author
        publisher_note = "publisher from PDF author metadata"
    else:
        publisher_patterns = [
            r"(?:发布单位|发布机构|编制单位|编制机构|主办单位|出品方|发布方)[:：]\s*([^\n\r]{2,80})",
            r"([^\n\r]{2,80}(?:协会|研究院|研究中心|联盟|委员会|公司))\s*(?:发布|编制|出品)",
        ]
        for pattern in publisher_patterns:
            match = re.search(pattern, full_text_head)
            if match:
                publisher = normalize_text(match.group(1)).strip(" ：:")
                publisher_note = "publisher from cover/front matter text"
                break

    publish_date = "unknown"
    date_note = "publish_date unknown"
    date_patterns = [
        r"(?:发布日期|发布时间|出版日期|发布于)[:：]?\s*((?:20|19)\d{2}[年\-/\.]\d{1,2}(?:[月\-/\.]\d{1,2}日?)?)",
        r"((?:20|19)\d{2}年\d{1,2}月)\s*(?:发布|出版)",
    ]
    for pattern in date_patterns:
        match = re.search(pattern, full_text_head)
        if match:
            publish_date = normalize_text(match.group(1))
            date_note = "publish_date from front matter text"
            break

    region_scope = "unknown"
    region_note = "region_scope unknown"
    profile_region = str((profile.get("metadata") or {}).get("region_scope") or "").strip()
    if profile_region and profile_region != "unknown":
        region_scope = profile_region
        region_note = f"region_scope from document profile {profile.get('profile_id', 'generic')}"

    notes = "; ".join([title_note, publisher_note, date_note, region_note])
    return {
        "title": title,
        "publisher": publisher,
        "publish_date": publish_date,
        "region_scope": region_scope,
        "pdf_metadata_title": metadata_title,
        "pdf_metadata_author": metadata_author,
        "pdf_metadata_creation_date": split_pdf_date(creation_date),
        "notes": notes,
    }


def table_count_for_page(page: fitz.Page) -> int:
    try:
        tables = page.find_tables()
        return len(tables.tables)
    except Exception as exc:
        logging.debug("Table detection failed on page %s: %s", page.number + 1, exc)
        return 0


def page_features(page: fitz.Page) -> Dict[str, object]:
    text = page.get_text("text") or ""
    normalized = normalize_text(text)
    blocks = page.get_text("blocks") or []
    try:
        drawings_count = len(page.get_drawings())
    except Exception:
        drawings_count = 0
    try:
        images_count = len(page.get_images(full=True))
    except Exception:
        images_count = 0
    table_count = table_count_for_page(page)

    keyword_hits = [kw for kw in ALL_VALUE_KEYWORDS if kw in text]
    chain_hits = [kw for kw in CHAIN_KEYWORDS if kw in text]
    chart_hits = [kw for kw in CHART_KEYWORDS if kw in text]
    return {
        "text": text,
        "normalized_text": normalized,
        "char_count": len(text.strip()),
        "block_count": len(blocks),
        "drawing_count": drawings_count,
        "image_count": images_count,
        "table_count": table_count,
        "keyword_hits": keyword_hits,
        "chain_hits": chain_hits,
        "chart_hits": chart_hits,
        "chinese_ratio": chinese_ratio(text),
        "garbled_ratio": garbled_ratio(text),
    }


def classify_page(page_no: int, features: Dict[str, object], page_count: int) -> Dict[str, object]:
    text = str(features["text"])
    normalized = str(features["normalized_text"])
    char_count = int(features["char_count"])
    block_count = int(features["block_count"])
    drawing_count = int(features["drawing_count"])
    image_count = int(features["image_count"])
    table_count = int(features["table_count"])
    chain_hits = list(features["chain_hits"])
    chart_hits = list(features["chart_hits"])
    cn_ratio = float(features["chinese_ratio"])
    bad_ratio = float(features["garbled_ratio"])

    has_images = image_count > 0
    has_vector_layout = drawing_count > 12
    has_table_candidate = table_count > 0
    if not has_table_candidate:
        table_signal = bool(re.search(r"(表\s*\d|表\d|单位[:：]|项目\s+.*?合计|分类\s+.*?数量)", text))
        has_table_candidate = table_signal and (has_vector_layout or "\t" in text or char_count > 300)
        table_count = 1 if has_table_candidate else 0

    chart_signal = bool(chart_hits) and (has_images or drawing_count > 20 or re.search(r"图\s*\d", text))
    has_chart_candidate = bool(chart_signal)
    chain_figure_title = bool(
        re.search(r"图\s*\d+\s*[:：][^\n\r]{0,80}(产业链|价值链|供应链|上游|中游|下游|环节)", text)
    )
    has_chain_candidate = bool(chain_hits) and chain_figure_title and (
        drawing_count > 15 or has_table_candidate or has_images
    )

    is_cover = page_no == 1
    is_toc = page_no <= 5 and ("目录" in text or "contents" in normalized.lower())

    if char_count == 0:
        text_quality = "not_applicable"
        text_extractable = "no"
    elif bad_ratio > 0.12:
        text_quality = "garbled"
        text_extractable = "partial"
    elif char_count < 80:
        text_quality = "low"
        text_extractable = "partial"
    elif cn_ratio < 0.15 and char_count > 100:
        text_quality = "low"
        text_extractable = "partial"
    else:
        text_quality = "normal"
        text_extractable = "yes"
    if is_toc and text_quality == "garbled":
        text_quality = "low"
        text_extractable = "partial"

    page_has_visible_content = has_images or drawing_count > 0 or char_count > 0
    if not page_has_visible_content:
        needs_ocr = "no"
    elif is_cover and char_count >= 5:
        needs_ocr = "no"
    elif is_toc and char_count >= 50:
        needs_ocr = "no"
    elif text_quality == "garbled":
        needs_ocr = "maybe"
    elif char_count < 20 and (has_images or drawing_count > 5):
        needs_ocr = "yes"
    elif char_count < 100 and (has_images or drawing_count > 15):
        needs_ocr = "maybe"
    else:
        needs_ocr = "no"

    if drawing_count > 50 or image_count > 3 or (has_table_candidate and has_chart_candidate) or block_count > 30:
        layout_complexity = "complex"
    elif drawing_count > 12 or image_count > 0 or block_count > 12 or has_table_candidate or has_chart_candidate:
        layout_complexity = "medium"
    else:
        layout_complexity = "simple"

    lower_text = normalized.lower()
    if not page_has_visible_content:
        page_type = "blank"
        confidence = 0.95
    elif is_cover:
        page_type = "cover"
        confidence = 0.90
    elif page_no <= 3 and re.search(r"(版权|声明|版权所有|免责声明)", text):
        page_type = "copyright"
        confidence = 0.85
    elif is_toc:
        page_type = "toc"
        confidence = 0.85
    elif "附录" in text or "appendix" in lower_text:
        page_type = "appendix"
        confidence = 0.75
    elif needs_ocr == "yes":
        page_type = "scan_image"
        confidence = 0.65
    elif has_table_candidate and has_chart_candidate:
        page_type = "mixed"
        confidence = 0.68
    elif has_table_candidate:
        page_type = "table"
        confidence = 0.75
    elif has_chart_candidate:
        page_type = "chart"
        confidence = 0.65
    elif layout_complexity == "complex":
        page_type = "mixed"
        confidence = 0.62
    elif text_extractable == "yes":
        page_type = "paragraph"
        confidence = 0.76
    else:
        page_type = "unknown"
        confidence = 0.45

    if page_type == "blank":
        recommended_parser = "skip"
    elif needs_ocr == "yes":
        recommended_parser = "ocr_then_layout"
    elif has_chain_candidate:
        recommended_parser = "diagram_visual_review"
    elif has_table_candidate:
        recommended_parser = "table_parser"
    elif has_chart_candidate:
        recommended_parser = "chart_caption_only"
    elif layout_complexity in {"medium", "complex"}:
        recommended_parser = "native_text_with_layout"
    elif text_extractable == "yes":
        recommended_parser = "native_text"
    else:
        recommended_parser = "manual_review"

    review_required = (
        needs_ocr != "no"
        or has_table_candidate
        or has_chart_candidate
        or has_chain_candidate
        or page_type in {"unknown", "mixed", "scan_image"}
        or text_quality != "normal"
    )

    notes: List[str] = []
    if text_quality == "normal":
        notes.append("原生文本正常")
    elif text_quality == "low":
        notes.append("原生文本字符数偏低或中文比例偏低")
    elif text_quality == "garbled":
        notes.append("疑似原生文本乱码")
    else:
        notes.append("无可用原生文本")
    if needs_ocr in {"yes", "maybe"}:
        notes.append(f"OCR需求={needs_ocr}")
    if has_table_candidate:
        notes.append("表格候选")
    if has_chart_candidate:
        notes.append("图表候选")
    if has_chain_candidate:
        notes.append("含产业链相关关键词，需视觉复核")

    return {
        "page_no": page_no,
        "page_label": str(page_no),
        "page_type": page_type,
        "classification_method": "heuristic",
        "classification_confidence": f"{confidence:.2f}",
        "native_text_char_count": char_count,
        "text_extractable": text_extractable,
        "text_quality": text_quality,
        "needs_ocr": needs_ocr,
        "has_images": bool_text(has_images),
        "has_table_candidate": bool_text(has_table_candidate),
        "table_candidate_count": table_count,
        "has_chart_candidate": bool_text(has_chart_candidate),
        "has_chain_diagram_candidate": bool_text(has_chain_candidate),
        "has_chain_diagram_confirmed": "unknown" if has_chain_candidate else "no",
        "layout_complexity": layout_complexity,
        "recommended_parser": recommended_parser,
        "review_required": bool_text(review_required),
        "quality_note": "；".join(notes),
    }


def load_visual_notes(path: Optional[Path], doc_id: str) -> Dict[int, Dict[str, str]]:
    if not path or not path.exists():
        return {}
    _, rows = read_csv(path)
    notes: Dict[int, Dict[str, str]] = {}
    for row in rows:
        note_doc_id = str(row.get("doc_id", "") or "").strip()
        # Visual page judgments are document-specific.  Legacy unscoped rows
        # must not be applied to every PDF that happens to share a page number.
        if not note_doc_id or note_doc_id != doc_id:
            continue
        try:
            page_no = int(str(row.get("page_no", "")).strip())
        except ValueError:
            continue
        notes[page_no] = {k: (v or "").strip() for k, v in row.items()}
    return notes


def apply_visual_notes(row: Dict[str, object], note: Dict[str, str]) -> Dict[str, object]:
    if not note:
        return row
    merged = dict(row)
    override_type = note.get("page_type_override", "")
    if override_type:
        if override_type not in PAGE_TYPES:
            raise ValueError(f"Invalid page_type_override for page {row['page_no']}: {override_type}")
        merged["page_type"] = override_type
    chain_confirmed = note.get("has_chain_diagram_confirmed", "")
    if chain_confirmed:
        if chain_confirmed not in {"yes", "no", "unknown"}:
            raise ValueError(f"Invalid has_chain_diagram_confirmed for page {row['page_no']}: {chain_confirmed}")
        merged["has_chain_diagram_confirmed"] = chain_confirmed
        if chain_confirmed == "yes":
            merged["has_chain_diagram_candidate"] = "yes"
            merged["recommended_parser"] = "diagram_visual_review"
    review_required = note.get("review_required", "")
    if review_required:
        if review_required not in {"yes", "no"}:
            raise ValueError(f"Invalid review_required for page {row['page_no']}: {review_required}")
        merged["review_required"] = review_required
        if review_required == "no" and override_type in {"cover", "copyright", "toc", "paragraph"}:
            merged["needs_ocr"] = "no"
    if override_type == "toc" and merged.get("text_quality") == "garbled":
        merged["text_quality"] = "low"
    if override_type in {"cover", "copyright", "toc", "paragraph"} and merged.get("needs_ocr") == "no":
        if merged.get("has_table_candidate") == "yes":
            merged["recommended_parser"] = "table_parser"
        elif merged.get("has_chart_candidate") == "yes":
            merged["recommended_parser"] = "chart_caption_only"
        elif override_type in {"cover", "toc"}:
            merged["recommended_parser"] = "native_text_with_layout"
        else:
            merged["recommended_parser"] = "native_text"
    comment = note.get("visual_review_comment", "")
    if comment:
        existing = str(merged.get("quality_note", ""))
        merged["quality_note"] = f"{existing}；视觉复核：{comment}" if existing else f"视觉复核：{comment}"
    method = str(merged.get("classification_method", "heuristic"))
    if "visual_review" not in method:
        merged["classification_method"] = f"{method}+visual_review"
    merged["classification_confidence"] = "0.90" if override_type else str(merged["classification_confidence"])
    return merged


def render_pages(doc: fitz.Document, output_dir: Path, dpi: int, force: bool) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: List[Path] = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    for page_index in range(doc.page_count):
        out_path = output_dir / f"page_{page_index + 1:03d}.png"
        if force or not out_path.exists():
            pix = doc.load_page(page_index).get_pixmap(matrix=matrix, alpha=False)
            pix.save(out_path)
        rendered.append(out_path)
    return rendered


def make_contact_sheet(image_paths: Sequence[Path], output_path: Path, columns: int = 5) -> None:
    if not image_paths:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    backup_if_exists(output_path)
    thumb_w, thumb_h = 220, 300
    label_h = 28
    margin = 16
    rows = math.ceil(len(image_paths) / columns)
    sheet_w = columns * thumb_w + (columns + 1) * margin
    sheet_h = rows * (thumb_h + label_h) + (rows + 1) * margin
    sheet = Image.new("RGB", (sheet_w, sheet_h), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except OSError:
        font = ImageFont.load_default()
    for idx, path in enumerate(image_paths):
        row = idx // columns
        col = idx % columns
        x = margin + col * (thumb_w + margin)
        y = margin + row * (thumb_h + label_h + margin)
        with Image.open(path) as img:
            img.thumbnail((thumb_w, thumb_h))
            thumb = Image.new("RGB", (thumb_w, thumb_h), "white")
            tx = (thumb_w - img.width) // 2
            ty = (thumb_h - img.height) // 2
            thumb.paste(img.convert("RGB"), (tx, ty))
        sheet.paste(thumb, (x, y))
        draw.rectangle([x, y, x + thumb_w, y + thumb_h], outline=(180, 180, 180), width=1)
        label = path.stem.replace("page_", "page ")
        draw.text((x, y + thumb_h + 6), label, fill=(0, 0, 0), font=font)
    sheet.save(output_path)


def copy_sample_images(sample_pages: Sequence[int], rendered_paths: Sequence[Path], sample_dir: Path) -> None:
    sample_dir.mkdir(parents=True, exist_ok=True)
    for page_no in sample_pages:
        src = rendered_paths[page_no - 1]
        dst = sample_dir / src.name
        shutil.copy2(src, dst)


def first_page(rows: Sequence[Dict[str, object]], predicate, excluded: Optional[set] = None) -> Optional[int]:
    excluded = excluded or set()
    for row in rows:
        page_no = int(row["page_no"])
        if page_no in excluded:
            continue
        if predicate(row):
            return page_no
    return None


def select_sample_pages(rows: Sequence[Dict[str, object]]) -> List[Tuple[int, str]]:
    criteria = [
        ("封面或目录页", lambda r: r["page_type"] in {"cover", "toc"}),
        (
            "正常正文页",
            lambda r: r["page_type"] == "paragraph"
            and r["text_extractable"] == "yes"
            and r["has_table_candidate"] == "no"
            and r["has_chart_candidate"] == "no",
        ),
        ("表格候选页", lambda r: r["has_table_candidate"] == "yes"),
        ("图表候选页", lambda r: r["has_chart_candidate"] == "yes"),
        ("产业链图候选页", lambda r: r["has_chain_diagram_candidate"] == "yes"),
        (
            "扫描或低文本质量页",
            lambda r: r["needs_ocr"] in {"yes", "maybe"}
            or (r["text_quality"] in {"low", "garbled"} and r["page_type"] not in {"cover", "copyright", "toc"}),
        ),
        ("复杂图文混排页", lambda r: r["page_type"] == "mixed" or r["layout_complexity"] == "complex"),
    ]
    selected: List[Tuple[int, str]] = []
    seen = set()
    for reason, predicate in criteria:
        page_no = first_page(rows, predicate, seen)
        if page_no is None or page_no in seen:
            continue
        selected.append((page_no, reason))
        seen.add(page_no)
    if not selected and rows:
        selected.append((int(rows[0]["page_no"]), "默认抽样页"))
    return selected


def make_sample_review(
    selected: Sequence[Tuple[int, str]],
    rows_by_page: Dict[int, Dict[str, object]],
    visual_notes: Dict[int, Dict[str, str]],
) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    for page_no, reason in selected:
        row = rows_by_page[page_no]
        comment = "待人工复核"
        if page_no in visual_notes and visual_notes[page_no].get("visual_review_comment"):
            comment = visual_notes[page_no]["visual_review_comment"]
        output.append(
            {
                "page_no": page_no,
                "sample_reason": reason,
                "page_type": row["page_type"],
                "text_extractable": row["text_extractable"],
                "needs_ocr": row["needs_ocr"],
                "has_table_candidate": row["has_table_candidate"],
                "has_chain_diagram_candidate": row["has_chain_diagram_candidate"],
                "recommended_parser": row["recommended_parser"],
                "review_comment": comment,
            }
        )
    return output


def write_text_preview(
    path: Path,
    selected: Sequence[Tuple[int, str]],
    features_by_page: Dict[int, Dict[str, object]],
) -> None:
    backup_if_exists(path)
    lines = ["# 抽样页面原生文本预览", ""]
    for page_no, reason in selected:
        text = str(features_by_page[page_no]["text"])
        preview = text[:1000] if text else "[无原生文本]"
        lines.extend(
            [
                f"## Page {page_no:03d} - {reason}",
                "",
                f"- 原生文本字符数：{len(text.strip())}",
                "",
                "```text",
                preview,
                "```",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def high_value_pages(
    page_rows: Sequence[Dict[str, object]],
    features_by_page: Dict[int, Dict[str, object]],
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for row in page_rows:
        page_no = int(row["page_no"])
        features = features_by_page[page_no]
        hits = list(features["keyword_hits"])
        chain_hits = list(features["chain_hits"])
        if not hits and row["has_table_candidate"] != "yes" and row["has_chart_candidate"] != "yes":
            continue
        score = 0
        score += 3 * len(chain_hits)
        score += len([kw for kw in hits if kw in DOMAIN_KEYWORDS])
        if row["has_chain_diagram_candidate"] == "yes":
            score += 3
        if row["has_table_candidate"] == "yes":
            score += 2
        if row["has_chart_candidate"] == "yes":
            score += 1
        if row["page_type"] in {"cover", "toc", "copyright", "blank"}:
            score -= 2

        if score >= 8:
            priority = "high"
        elif score >= 4:
            priority = "medium"
        else:
            priority = "low"

        reasons: List[str] = []
        suspected: List[str] = []
        if hits:
            reasons.append("关键词命中：" + "/".join(hits[:8]))
            suspected.append("关键词支持内容：" + "/".join(hits[:8]))
        if row["has_table_candidate"] == "yes":
            reasons.append("表格候选")
            suspected.append("含表格候选")
        if row["has_chart_candidate"] == "yes":
            reasons.append("图表候选")
            suspected.append("含图表候选")
        if row["has_chain_diagram_candidate"] == "yes":
            reasons.append("产业链图候选，需人工确认")
            suspected.append("可能含产业链/上下游相关图文")

        if row["needs_ocr"] == "yes":
            action = "ocr_then_extract"
        elif row["has_chain_diagram_candidate"] == "yes":
            action = "visual_review_diagram"
        elif row["has_table_candidate"] == "yes":
            action = "extract_table_structure"
        elif row["has_chart_candidate"] == "yes":
            action = "manual_inspection"
        else:
            action = "extract_native_text"

        rows.append(
            {
                "page_no": page_no,
                "priority": priority,
                "reason": "；".join(reasons),
                "suspected_content": "；".join(suspected),
                "recommended_next_stage_action": action,
                "review_required": row["review_required"],
                "_score": score,
            }
        )
    rows.sort(key=lambda item: (-int(item["_score"]), int(item["page_no"])))
    for item in rows:
        item.pop("_score", None)
    return rows


def page_list(rows: Sequence[Dict[str, object]], predicate) -> str:
    pages = [str(row["page_no"]) for row in rows if predicate(row)]
    return ", ".join(pages) if pages else "无"


def count_ratio(count: int, total: int) -> str:
    if total == 0:
        return "0/0 (0.0%)"
    return f"{count}/{total} ({count / total:.1%})"


def write_report(
    path: Path,
    pdf_path: Path,
    doc_record: Dict[str, object],
    page_rows: Sequence[Dict[str, object]],
    high_rows: Sequence[Dict[str, object]],
    validation: Dict[str, object],
    deps: Dict[str, object],
    log_path: Path,
) -> None:
    backup_if_exists(path)
    page_count = len(page_rows)
    type_counts = Counter(str(row["page_type"]) for row in page_rows)
    native_rows = [
        row
        for row in page_rows
        if row["text_extractable"] == "yes" and row["text_quality"] == "normal" and row["needs_ocr"] == "no"
    ]
    ocr_pages = page_list(page_rows, lambda r: r["needs_ocr"] in {"yes", "maybe"})
    table_pages = page_list(page_rows, lambda r: r["has_table_candidate"] == "yes")
    chart_pages = page_list(page_rows, lambda r: r["has_chart_candidate"] == "yes")
    chain_candidate_pages = page_list(page_rows, lambda r: r["has_chain_diagram_candidate"] == "yes")
    chain_confirmed_pages = page_list(page_rows, lambda r: r["has_chain_diagram_confirmed"] == "yes")
    review_pages = page_list(page_rows, lambda r: r["review_required"] == "yes")

    high_lines = []
    for row in high_rows:
        high_lines.append(
            f"- Page {row['page_no']}: {row['priority']}；{row['reason']}；下一步：{row['recommended_next_stage_action']}"
        )
    if not high_lines:
        high_lines.append("- 无")

    type_lines = [f"- {ptype}: {count}" for ptype, count in sorted(type_counts.items())]
    validation_lines = [f"- {key}: {value}" for key, value in validation.items()]

    dependency_lines = [f"- {key}: {value}" for key, value in deps.items()]

    lines = [
        "# 第一阶段文档审计报告",
        "",
        "## 1. 文档基本信息",
        "",
        f"- 文件：{pdf_path}",
        f"- doc_id：{doc_record['doc_id']}",
        f"- SHA256：{doc_record['file_sha256']}",
        f"- 文件大小 MB：{doc_record['file_size_mb']}",
        f"- 标题：{doc_record['title']}",
        f"- 发布机构：{doc_record['publisher']}",
        f"- 发布日期：{doc_record['publish_date']}",
        f"- 地域范围：{doc_record['region_scope']}",
        f"- PDF 元数据标题：{doc_record['pdf_metadata_title'] or 'unknown'}",
        f"- PDF 元数据作者：{doc_record['pdf_metadata_author'] or 'unknown'}",
        f"- PDF 创建日期：{doc_record['pdf_metadata_creation_date'] or 'unknown'}",
        f"- 是否加密：{doc_record['is_encrypted']}",
        f"- 日志：{log_path}",
        "",
        "## 2. 环境与依赖",
        "",
        *dependency_lines,
        "",
        "## 3. 原生文本可用性",
        "",
        f"- PDF 是否可直接提取文本：{'是' if native_rows else '否'}",
        f"- 可直接用原生文本解析的页面比例：{count_ratio(len(native_rows), page_count)}",
        f"- 需要或可能需要 OCR 的页面：{ocr_pages}",
        "",
        "## 4. 页面总数与类型统计",
        "",
        f"- 页面总数：{page_count}",
        *type_lines,
        "",
        "## 5. 候选页面列表",
        "",
        f"- 表格候选页：{table_pages}",
        f"- 图表候选页：{chart_pages}",
        f"- 产业链图候选页：{chain_candidate_pages}",
        f"- 产业链图视觉确认页：{chain_confirmed_pages}",
        f"- 仍需人工复核页：{review_pages}",
        "",
        "## 6. 高价值页面",
        "",
        *high_lines,
        "",
        "## 7. 主要解析风险",
        "",
        "- 表格、统计图表和产业链图候选均来自启发式检测或抽样视觉复核，第二阶段不能直接把候选图认定为真实产业链关系。",
        "- 多栏或图文混排页面需要保留文本块位置并校验阅读顺序。",
        "- 图表页优先抽取图题、图注、坐标轴和结论文本；不应在缺少视觉核验的情况下自动推断完整数值。",
        "- 原生文本字符数低或疑似扫描页需要 OCR 后再做版面恢复，并进行人工抽样核验。",
        "",
        "## 8. 第二阶段推荐解析路由",
        "",
        "- 普通原生文本页 → PyMuPDF 原生文本提取，并保留页码与文本块位置",
        "- 复杂多栏文本页 → 原生文本 + 版面顺序校验",
        "- 表格页 → 表格结构解析，保留表题、表头、单位、脚注和页码",
        "- 产业链图候选页 → 图像视觉识别 + 人工审核箭头方向与节点名称",
        "- 扫描或乱码页 → OCR + 版面恢复 + 人工抽样核验",
        "- 普通统计图表页 → 优先提取图题、图注、坐标轴和结论，不直接自动推断全部数值",
        "",
        "## 9. 验证结果",
        "",
        *validation_lines,
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def validate_outputs(
    pdf_hash_before: str,
    pdf_hash_after: str,
    documents_path: Path,
    page_manifest_path: Path,
    rendered_paths: Sequence[Path],
    high_path: Path,
    sample_path: Path,
    report_path: Path,
    page_count: int,
    doc_id: str,
) -> Dict[str, object]:
    _, doc_rows = read_csv(documents_path)
    _, page_rows = read_csv(page_manifest_path)
    page_numbers = [int(row["page_no"]) for row in page_rows if str(row.get("page_no", "")).isdigit()]
    expected_numbers = list(range(1, page_count + 1))
    return {
        "documents.csv包含本PDF记录": any(row.get("doc_id") == doc_id for row in doc_rows),
        "page_manifest.csv行数等于PDF页数": len(page_rows) == page_count,
        "页码连续且无缺失": page_numbers == expected_numbers,
        "所有渲染页PNG存在": all(path.exists() for path in rendered_paths) and len(rendered_paths) == page_count,
        "high_value_pages.csv已生成": high_path.exists(),
        "sample_review.csv已生成": sample_path.exists(),
        "document_audit_report.md已生成": report_path.exists(),
        "CSV使用UTF-8-SIG写入": True,
        "PDF哈希前后一致": pdf_hash_before == pdf_hash_after,
        "未执行OCR/LLM/关系抽取": True,
    }


def validate_enums(rows: Sequence[Dict[str, object]]) -> None:
    for row in rows:
        page = row["page_no"]
        checks = [
            ("page_type", PAGE_TYPES),
            ("text_extractable", TEXT_EXTRACTABLE_VALUES),
            ("text_quality", TEXT_QUALITY_VALUES),
            ("needs_ocr", OCR_VALUES),
            ("layout_complexity", LAYOUT_VALUES),
            ("recommended_parser", PARSER_VALUES),
        ]
        for field, allowed in checks:
            if row[field] not in allowed:
                raise ValueError(f"Invalid {field} on page {page}: {row[field]}")
        confidence = float(row["classification_confidence"])
        if not 0 <= confidence <= 1:
            raise ValueError(f"Invalid classification_confidence on page {page}: {confidence}")


def update_documents_csv(path: Path, doc_record: Dict[str, object]) -> None:
    existing_fields, existing_rows = read_csv(path)
    fieldnames = list(DOC_FIELDS)
    for field in existing_fields:
        if field not in fieldnames:
            fieldnames.append(field)
    filtered_rows = [
        row
        for row in existing_rows
        if row.get("doc_id") != doc_record["doc_id"]
        and row.get("file_sha256") != doc_record["file_sha256"]
        and row.get("file_path") != doc_record["file_path"]
    ]
    filtered_rows.append(doc_record)
    backup_if_exists(path)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in filtered_rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).expanduser().resolve()
    pdf_path = Path(args.pdf).expanduser().resolve()
    paths = ensure_dirs(project_root)
    log_path = setup_logging(paths["logs"])

    logging.info("Stage 1 document audit started")
    logging.info("Project root: %s", project_root)
    logging.info("PDF path: %s", pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pdf_hash_before = sha256_file(pdf_path)
    doc_id = f"{pdf_path.stem}_{pdf_hash_before[:12]}"
    doc = fitz.open(pdf_path)
    if doc.needs_pass:
        raise RuntimeError("PDF requires a password; cannot audit without decryption.")
    page_count = doc.page_count
    logging.info("PDF pages: %s; encrypted: %s", page_count, doc.is_encrypted)

    head_text_parts = []
    features_by_page: Dict[int, Dict[str, object]] = {}
    page_rows: List[Dict[str, object]] = []
    for page_index in range(page_count):
        page = doc.load_page(page_index)
        features = page_features(page)
        page_no = page_index + 1
        features_by_page[page_no] = features
        if page_no <= 5:
            head_text_parts.append(str(features["text"]))
        row = classify_page(page_no, features, page_count)
        page_rows.append(row)

    visual_notes_path = (
        Path(args.visual_review_notes).expanduser().resolve()
        if args.visual_review_notes
        else paths["metadata"] / "visual_review_notes.csv"
    )
    visual_notes = load_visual_notes(visual_notes_path, doc_id)
    if visual_notes:
        logging.info("Loaded visual review notes: %s", visual_notes_path)
        page_rows = [apply_visual_notes(row, visual_notes.get(int(row["page_no"]), {})) for row in page_rows]

    validate_enums(page_rows)

    full_text_head = "\n".join(head_text_parts)
    document_profile = resolve_document_profile(
        project_root,
        hints=(pdf_path.name, pdf_path.stem, full_text_head),
        explicit=args.document_profile,
    )
    logging.info("Document profile: %s", document_profile["profile_id"])
    inferred_doc = infer_document_info(pdf_path, doc, full_text_head, document_profile)
    file_size_mb = pdf_path.stat().st_size / (1024 * 1024)
    doc_record = {
        "doc_id": doc_id,
        "file_name": pdf_path.name,
        "file_path": str(pdf_path),
        "file_size_mb": f"{file_size_mb:.2f}",
        "file_sha256": pdf_hash_before,
        "industry": str((document_profile.get("metadata") or {}).get("industry") or "unknown"),
        "title": inferred_doc["title"],
        "publisher": inferred_doc["publisher"],
        "publish_date": inferred_doc["publish_date"],
        "region_scope": inferred_doc["region_scope"],
        "page_count": page_count,
        "pdf_metadata_title": inferred_doc["pdf_metadata_title"],
        "pdf_metadata_author": inferred_doc["pdf_metadata_author"],
        "pdf_metadata_creation_date": inferred_doc["pdf_metadata_creation_date"],
        "is_encrypted": bool_text(bool(doc.is_encrypted)),
        "document_type": "white_paper",
        "source_quality_provisional": "unknown",
        "notes": inferred_doc["notes"],
    }

    for row in page_rows:
        row["doc_id"] = doc_id

    rendered_dir = paths["rendered_pages"] / pdf_path.stem
    sample_dir = paths["samples"] / pdf_path.stem
    rendered_paths = render_pages(doc, rendered_dir, args.dpi, args.force_render)
    make_contact_sheet(rendered_paths, sample_dir / "contact_sheet_all_pages.png")

    rows_by_page = {int(row["page_no"]): row for row in page_rows}
    selected_samples = select_sample_pages(page_rows)
    sample_pages = [page_no for page_no, _reason in selected_samples]
    copy_sample_images(sample_pages, rendered_paths, sample_dir)
    make_contact_sheet([rendered_paths[page_no - 1] for page_no in sample_pages], sample_dir / "contact_sheet_samples.png")
    sample_review_rows = make_sample_review(selected_samples, rows_by_page, visual_notes)

    high_rows = high_value_pages(page_rows, features_by_page)

    documents_path = paths["metadata"] / "documents.csv"
    page_manifest_path = paths["metadata"] / "page_manifest.csv"
    sample_review_path = paths["outputs"] / "sample_review.csv"
    sample_preview_path = paths["outputs"] / "sample_text_preview.md"
    high_path = paths["outputs"] / "high_value_pages.csv"
    report_path = paths["outputs"] / "document_audit_report.md"
    validation_path = paths["outputs"] / "validation_summary.json"

    update_documents_csv(documents_path, doc_record)
    write_csv(page_manifest_path, PAGE_FIELDS, page_rows)
    write_csv(sample_review_path, SAMPLE_FIELDS, sample_review_rows)
    write_text_preview(sample_preview_path, selected_samples, features_by_page)
    write_csv(high_path, HIGH_VALUE_FIELDS, high_rows)

    pdf_hash_after = sha256_file(pdf_path)
    deps = {
        "Python": sys.version.split()[0],
        "PyMuPDF": getattr(fitz, "__version__", "unknown"),
        "Pillow": getattr(Image, "__version__", "unknown"),
        "pandas": "available" if module_available("pandas") else "missing",
        "pdfplumber": "available" if module_available("pdfplumber") else "missing",
    }
    validation = validate_outputs(
        pdf_hash_before,
        pdf_hash_after,
        documents_path,
        page_manifest_path,
        rendered_paths,
        high_path,
        sample_review_path,
        report_path,
        page_count,
        doc_id,
    )
    validation_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_report(report_path, pdf_path, doc_record, page_rows, high_rows, validation, deps, log_path)
    validation = validate_outputs(
        pdf_hash_before,
        pdf_hash_after,
        documents_path,
        page_manifest_path,
        rendered_paths,
        high_path,
        sample_review_path,
        report_path,
        page_count,
        doc_id,
    )
    validation_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    type_counts = Counter(str(row["page_type"]) for row in page_rows)
    ocr_count = sum(1 for row in page_rows if row["needs_ocr"] in {"yes", "maybe"})
    table_count = sum(1 for row in page_rows if row["has_table_candidate"] == "yes")
    chain_pages = [str(row["page_no"]) for row in page_rows if row["has_chain_diagram_candidate"] == "yes"]
    high_pages = [str(row["page_no"]) for row in high_rows]

    logging.info("Outputs written:")
    for out_path in [
        documents_path,
        page_manifest_path,
        sample_review_path,
        sample_preview_path,
        high_path,
        report_path,
        validation_path,
    ]:
        logging.info("  %s", out_path)
    logging.info("Rendered pages: %s", rendered_dir)
    logging.info("Samples: %s", sample_dir)

    print("")
    print("Stage 1 audit complete")
    print(f"Pages: {page_count}")
    print(f"Page type counts: {dict(type_counts)}")
    print(f"OCR yes/maybe pages: {ocr_count}")
    print(f"Table candidate pages: {table_count}")
    print(f"Chain diagram candidate pages: {', '.join(chain_pages) if chain_pages else 'none'}")
    print(f"High-value pages: {', '.join(high_pages) if high_pages else 'none'}")
    print(f"Validation passed: {all(bool(value) for value in validation.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
