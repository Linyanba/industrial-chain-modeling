#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build a traceable structured evidence store from an audited PDF.

Stage 2 deliberately does not run OCR, call external APIs/LLMs, infer entities,
or extract industry-chain relations. It only preserves native evidence with
document/page/block/table/figure traceability.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import logging
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from document_profile_manager import document_rules, resolve_document_profile

try:
    import fitz  # PyMuPDF
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit("Missing required dependency: PyMuPDF / fitz") from exc

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit("Missing required dependency: Pillow") from exc


PAGE_RECORD_FIELDS = [
    "doc_id",
    "page_no",
    "page_type",
    "text_extractable",
    "text_quality",
    "needs_ocr",
    "has_table_candidate",
    "has_chart_candidate",
    "has_chain_diagram_candidate",
    "has_chain_diagram_confirmed",
    "recommended_parser",
    "review_required",
]

TABLE_MANIFEST_FIELDS = [
    "table_id",
    "page_no",
    "caption",
    "headers_preview",
    "unit",
    "footnote_present",
    "extraction_method",
    "parse_quality",
    "csv_path",
    "html_path",
    "json_path",
    "review_required",
    "quality_note",
]

FIGURE_MANIFEST_FIELDS = [
    "figure_id",
    "page_no",
    "figure_type",
    "is_chain_diagram_candidate",
    "is_chain_diagram_confirmed",
    "caption",
    "caption_source",
    "nearby_text_evidence_ids",
    "page_image_path",
    "figure_image_path",
    "bbox",
    "source_method",
    "review_required",
    "quality_note",
]

UNRESOLVED_FIELDS = [
    "item_id",
    "item_type",
    "page_no",
    "reason",
    "severity",
    "recommended_action",
    "related_output_path",
]

VALUE_KEYWORDS = [
    "产业结构",
    "产业链",
    "价值链",
    "供应链",
    "上游",
    "中游",
    "下游",
    "材料",
    "设备",
    "设计",
    "制造",
    "封装",
    "测试",
    "应用",
    "国产替代",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build stage 2 evidence store")
    parser.add_argument("--pdf", required=True, help="Path to source PDF")
    parser.add_argument("--project-root", required=True, help="Project root")
    parser.add_argument("--document-profile", default="auto", help="Document profile id or auto")
    return parser.parse_args()


def setup_logging(project_root: Path) -> Path:
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"stage2_build_evidence_{stamp}.log"
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


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required CSV not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def relpath(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def rendered_pages_dir(project_root: Path, doc_id: str) -> Path:
    doc_stem = doc_id.rsplit("_", 1)[0]
    return project_root / "data" / "rendered_pages" / doc_stem


def normalize_text(text: str) -> str:
    text = (text or "").replace("\u0000", "")
    text = text.replace("\b", " ")
    # PDF TOC dotted leaders can be extracted as repeated replacement glyphs.
    # Keep the page target, but collapse the damaged leader itself.
    text = re.sub(r"(?:[ .]*�[ .]*){3,}(pg\.?\s*\d+)", r" …… \1", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:[ .]*�[ .]*){3,}", " …… ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\n(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def garbled_score(text: str) -> float:
    chars = [ch for ch in text or "" if not ch.isspace()]
    if not chars:
        return 0.0
    suspicious = 0
    for ch in chars:
        code = ord(ch)
        if ch == "\ufffd":
            suspicious += 1
        elif 0x0400 <= code <= 0x04FF:  # Cyrillic-like artifacts in this PDF
            suspicious += 1
        elif 0x3400 <= code <= 0x4DBF:  # CJK Extension A, mostly damaged glyph maps here
            suspicious += 1
        elif 0x2300 <= code <= 0x2BFF:  # misc symbols/arrows used by damaged chart text
            suspicious += 1
    return suspicious / len(chars)


def one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def text_quality(text: str, page_quality: str) -> str:
    if page_quality in {"garbled", "low"}:
        return page_quality
    stripped = text.strip()
    if not stripped:
        return "low"
    if "�" in stripped or garbled_score(stripped) >= 0.18:
        return "garbled"
    return "normal"


def module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def find_doc_record(documents: Sequence[Dict[str, str]], pdf_path: Path, pdf_hash: str) -> Dict[str, str]:
    pdf_norm = str(pdf_path.resolve()).lower()
    for row in documents:
        if (row.get("file_sha256") or "").lower() == pdf_hash.lower():
            return row
    for row in documents:
        if (row.get("file_path") or "").lower() == pdf_norm:
            return row
    raise RuntimeError("Could not locate this PDF in documents.csv by SHA256 or file_path")


def make_run_dirs(project_root: Path, doc_id: str) -> Tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parsed_parent = project_root / "parsed_documents"
    output_parent = project_root / "outputs" / "stage2_evidence_build"
    parsed_parent.mkdir(parents=True, exist_ok=True)
    output_parent.mkdir(parents=True, exist_ok=True)

    canonical = parsed_parent / doc_id
    parsed_run = canonical if not canonical.exists() else parsed_parent / f"{doc_id}_{stamp}"
    output_run = output_parent / f"{doc_id}_{stamp}"
    for path in [
        parsed_run,
        parsed_run / "page_records",
        parsed_run / "tables",
        parsed_run / "figures",
        output_run,
    ]:
        path.mkdir(parents=True, exist_ok=True)

    (parsed_parent / "latest_run.txt").write_text(str(parsed_run), encoding="utf-8")
    (output_parent / "latest_run.txt").write_text(str(output_run), encoding="utf-8")
    (output_run / "latest_run.txt").write_text(
        f"parsed_run={parsed_run}\noutput_run={output_run}\n", encoding="utf-8"
    )
    return parsed_run, output_run


def estimate_span_features(block: Dict[str, Any]) -> Tuple[float, bool]:
    sizes: List[float] = []
    bold = False
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            if span.get("text", "").strip():
                sizes.append(float(span.get("size", 0.0)))
                font = str(span.get("font", "")).lower()
                if "bold" in font or "heavy" in font or "black" in font or "semibold" in font:
                    bold = True
    avg_size = sum(sizes) / len(sizes) if sizes else 0.0
    return avg_size, bold


def classify_block_role(
    text: str,
    bbox: Sequence[float],
    page_height: float,
    font_size: float,
    bold: bool,
    page_type: str,
    header_footer_markers: Sequence[str] = (),
) -> str:
    normalized = one_line(text)
    x0, y0, x1, y1 = [float(v) for v in bbox]
    if not normalized:
        return "unknown"
    if y0 < 80 and any(marker.casefold() in normalized.casefold() for marker in header_footer_markers):
        return "header"
    if y1 > page_height - 65:
        if re.fullmatch(r"\d{1,3}", normalized) or "版权所有" in normalized or "二维码" in normalized:
            return "footer"
    if re.match(r"^(图|表)\s*\d+\s*[:：]", normalized) or normalized.startswith(("注：", "资料来源", "来源：")):
        return "caption"
    if page_type in {"cover", "toc", "copyright"} and len(normalized) < 80:
        return "heading" if page_type in {"cover", "toc"} else "footer"
    if re.match(r"^(\d+\.|\d+\.\d+|\d+\.\d+\.\d+)\s*\S+", normalized):
        return "heading"
    if re.match(r"^(寄语|摘要|启示)$", normalized):
        return "heading"
    if font_size >= 16 or (bold and len(normalized) <= 40):
        return "heading"
    return "paragraph"


def native_block_requires_review(
    manifest: Dict[str, str], role: str, text_normalized: str, block_text_quality: str,
) -> bool:
    """细化图表页复核：只放行足够长且质量正常的原生正文，不放行图内短标签。"""
    unsafe_text = (
        manifest.get("text_quality") in {"low", "garbled"}
        or block_text_quality in {"low", "garbled"}
        or manifest.get("needs_ocr") in {"yes", "maybe"}
    )
    if unsafe_text:
        return True
    page_type = manifest.get("page_type", "")
    if page_type in {"mixed", "chain_diagram", "table", "scan_image", "unknown"}:
        return True
    safe_chart_prose = (
        page_type == "chart"
        and role == "paragraph"
        and len(one_line(text_normalized)) >= 40
    )
    if manifest.get("review_required") == "yes" and not safe_chart_prose:
        return True
    return False


def heading_level(text: str) -> int:
    normalized = one_line(text)
    if re.match(r"^\d+\.\d+\.\d+\s+", normalized):
        return 3
    if re.match(r"^\d+\.\d+\s+", normalized):
        return 2
    if re.match(r"^\d+\.\s+", normalized):
        return 1
    if normalized in {"寄语", "摘要"}:
        return 1
    return 2


def is_section_heading(block: Dict[str, Any], page_type: str) -> bool:
    if block["block_role"] != "heading":
        return False
    text = one_line(block["text_normalized"])
    if not text or page_type in {"cover", "toc", "copyright"}:
        return False
    if len(text) > 80:
        return False
    if re.match(r"^(\d+\.|\d+\.\d+|\d+\.\d+\.\d+)\s*\S+", text):
        return True
    if text in {"寄语", "摘要", "启示"}:
        return True
    return bool(block["is_bold_estimate"] and block["font_size_estimate"] >= 12 and len(text) <= 32)


def extract_page_blocks(
    doc: fitz.Document,
    page_manifest: Dict[int, Dict[str, str]],
    doc_id: str,
    header_footer_markers: Sequence[str] = (),
) -> Tuple[Dict[int, Dict[str, Any]], List[Dict[str, Any]]]:
    page_records: Dict[int, Dict[str, Any]] = {}
    all_blocks: List[Dict[str, Any]] = []
    for page_index in range(doc.page_count):
        page_no = page_index + 1
        manifest = page_manifest[page_no]
        page = doc.load_page(page_index)
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        page_record = {
            "doc_id": doc_id,
            "page_no": page_no,
            "page_type": manifest["page_type"],
            "text_extractable": manifest["text_extractable"],
            "text_quality": manifest["text_quality"],
            "needs_ocr": manifest["needs_ocr"],
            "recommended_parser": manifest["recommended_parser"],
            "review_required": manifest["review_required"] == "yes",
            "page_width": page_width,
            "page_height": page_height,
            "stage2_note": "",
            "blocks": [],
        }
        text_dict = page.get_text("dict")
        text_blocks = [b for b in text_dict.get("blocks", []) if b.get("type") == 0]
        sortable: List[Tuple[float, float, Dict[str, Any]]] = []
        for block in text_blocks:
            bbox = [float(v) for v in block.get("bbox", [0, 0, 0, 0])]
            sortable.append((bbox[1], bbox[0], block))
        sortable.sort(key=lambda item: (round(item[0], 1), round(item[1], 1)))

        for order, (_y, _x, raw_block) in enumerate(sortable, start=1):
            text_raw = "".join(
                span.get("text", "")
                for line in raw_block.get("lines", [])
                for span in line.get("spans", [])
            )
            if not text_raw.strip():
                continue
            text_norm = normalize_text(text_raw)
            bbox = [round(float(v), 2) for v in raw_block.get("bbox", [0, 0, 0, 0])]
            font_size, is_bold = estimate_span_features(raw_block)
            role = classify_block_role(
                text_raw, bbox, page_height, font_size, is_bold, manifest["page_type"],
                header_footer_markers,
            )
            block_text_quality = text_quality(text_norm, manifest["text_quality"])
            block_id = f"{doc_id}_p{page_no:03d}_b{order:03d}"
            block_record = {
                "block_id": block_id,
                "doc_id": doc_id,
                "page_no": page_no,
                "reading_order": order,
                "text_raw": text_raw,
                "text_normalized": text_norm,
                "bbox": bbox,
                "font_size_estimate": round(font_size, 2),
                "is_bold_estimate": bool(is_bold),
                "block_role": role,
                "section_path": ["unknown_section"],
                "text_quality": block_text_quality,
                "source_parser": "pymupdf_native",
                "review_required": native_block_requires_review(
                    manifest, role, text_norm, block_text_quality,
                ),
            }
            page_record["blocks"].append(block_record)
            all_blocks.append(block_record)
        page_records[page_no] = page_record
    return page_records, all_blocks


def build_sections_and_assign(
    page_records: Dict[int, Dict[str, Any]],
    all_blocks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    open_stack: List[Dict[str, Any]] = []
    current_path = ["unknown_section"]
    section_counter = 0

    def close_sections(level: int, end_page: int) -> None:
        while open_stack and int(open_stack[-1]["level"]) >= level:
            sec = open_stack.pop()
            sec["end_page"] = max(sec["start_page"], end_page)

    for block in all_blocks:
        page_no = int(block["page_no"])
        page_type = page_records[page_no]["page_type"]
        if is_section_heading(block, page_type):
            level = heading_level(block["text_normalized"])
            close_sections(level, page_no)
            parent_id = None
            for sec in reversed(open_stack):
                if int(sec["level"]) < level:
                    parent_id = sec["section_id"]
                    break
            section_counter += 1
            title = one_line(block["text_normalized"])
            section = {
                "section_id": f"sec_{section_counter:03d}",
                "title": title,
                "level": level,
                "start_page": page_no,
                "end_page": page_no,
                "parent_section_id": parent_id,
                "confidence": 0.85 if re.match(r"^\d", title) else 0.70,
            }
            sections.append(section)
            open_stack.append(section)
        if open_stack:
            for sec in open_stack:
                sec["end_page"] = page_no
            current_path = [str(sec["title"]) for sec in open_stack]
        else:
            current_path = ["unknown_section"]
        block["section_path"] = current_path[:]

    for sec in open_stack:
        sec["end_page"] = max(sec["end_page"], sec["start_page"])

    sections_by_page: Dict[int, List[str]] = {}
    for page_no, record in page_records.items():
        block_paths = [
            tuple(block["section_path"])
            for block in record["blocks"]
            if block.get("section_path") and block["section_path"] != ["unknown_section"]
        ]
        if block_paths:
            sections_by_page[page_no] = list(block_paths[-1])
        else:
            sections_by_page[page_no] = ["unknown_section"]
        record["section_path"] = sections_by_page[page_no]
    return sections


def build_document_structure(
    doc_id: str,
    pdf_path: Path,
    doc_record: Dict[str, str],
    page_manifest: Dict[int, Dict[str, str]],
    page_records: Dict[int, Dict[str, Any]],
    sections: Sequence[Dict[str, Any]],
    page_count: int,
) -> Dict[str, Any]:
    pages = []
    for page_no in range(1, page_count + 1):
        manifest = page_manifest[page_no]
        pages.append(
            {
                "page_no": page_no,
                "page_type": manifest["page_type"],
                "section_path": page_records[page_no].get("section_path", ["unknown_section"]),
                "text_extractable": manifest["text_extractable"],
                "review_required": manifest["review_required"] == "yes",
                "recommended_parser": manifest["recommended_parser"],
            }
        )
    return {
        "doc_id": doc_id,
        "file_name": pdf_path.name,
        "page_count": page_count,
        "document_title": doc_record.get("title") or "unknown",
        "sections": list(sections),
        "pages": pages,
    }


def split_long_text(text: str, max_chars: int = 1500) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    parts: List[str] = []
    current = ""
    for piece in re.split(r"(?<=[。！？；])", text):
        if not piece:
            continue
        if current and len(current) + len(piece) > max_chars:
            parts.append(current.strip())
            current = piece
        else:
            current += piece
    if current.strip():
        parts.append(current.strip())
    return parts or [text[:max_chars]]


def stage3_priority(page_no: int, text: str, page_type: str, high_priority: Dict[int, str]) -> str:
    if page_type in {"cover", "toc", "copyright", "blank"}:
        return "low"
    if high_priority.get(page_no) == "high":
        return "high"
    if any(keyword in text for keyword in VALUE_KEYWORDS):
        return "medium"
    return "low"


def build_evidence_chunks(
    doc_id: str,
    page_records: Dict[int, Dict[str, Any]],
    high_priority: Dict[int, str],
) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    counters: Counter[str] = Counter()
    for page_no in sorted(page_records):
        page = page_records[page_no]
        grouped_blocks: List[List[Dict[str, Any]]] = []
        pending_paragraphs: List[Dict[str, Any]] = []

        def flush_paragraphs() -> None:
            if pending_paragraphs:
                grouped_blocks.append(list(pending_paragraphs))
                pending_paragraphs.clear()

        for block in page["blocks"]:
            if block["block_role"] in {"header", "footer"}:
                continue
            text_norm = block["text_normalized"]
            if not text_norm:
                continue
            if block["block_role"] != "paragraph":
                flush_paragraphs()
                grouped_blocks.append([block])
                continue
            if pending_paragraphs and block.get("section_path") != pending_paragraphs[-1].get("section_path"):
                flush_paragraphs()
            pending_paragraphs.append(block)
            combined = "".join(str(x.get("text_normalized", "")) for x in pending_paragraphs)
            # PDF native extraction often emits one visual line per block.
            # Merge lines until a sentence boundary so relation endpoints and
            # predicates remain in the same traceable evidence chunk.
            if re.search(r"[。！？；.!?]\s*$", text_norm) or len(combined) >= 600:
                flush_paragraphs()
        flush_paragraphs()

        for group in grouped_blocks:
            block = group[0]
            text_norm = "".join(str(x.get("text_normalized", "")) for x in group)
            content_type = "paragraph"
            if block["block_role"] == "heading":
                content_type = "heading"
            elif block["block_role"] == "caption":
                content_type = "caption"
            for part in split_long_text(text_norm):
                counters[f"{page_no}_{content_type}"] += 1
                evidence_id = (
                    f"{doc_id}_p{page_no:03d}_{content_type}_{counters[f'{page_no}_{content_type}']:03d}"
                )
                chunks.append(
                    {
                        "evidence_id": evidence_id,
                        "doc_id": doc_id,
                        "page_start": page_no,
                        "page_end": page_no,
                        "section_path": block["section_path"],
                        "content_type": content_type,
                        "title_context": block["section_path"][-1]
                        if block["section_path"] != ["unknown_section"]
                        else "unknown",
                        "text_raw": "".join(str(x.get("text_raw", "")) for x in group),
                        "text_normalized": part,
                        "source_block_ids": [x["block_id"] for x in group],
                        "bbox_list": [x["bbox"] for x in group],
                        "source_parser": "pymupdf_native",
                        "parse_quality": 0.35
                        if any(x["text_quality"] == "garbled" for x in group)
                        else 0.85
                        if all(x["text_quality"] == "normal" and not x["review_required"] for x in group)
                        else 0.65,
                        "stage3_priority": stage3_priority(
                            page_no, text_norm, page["page_type"], high_priority
                        ),
                        "review_required": any(bool(x["review_required"]) for x in group),
                        "notes": "疑似PDF字体映射乱码，需回看页面图或人工复核。"
                        if any(x["text_quality"] == "garbled" for x in group)
                        else "",
                    }
                )
    return chunks


def page_caption_blocks(page_records: Dict[int, Dict[str, Any]], page_no: int) -> List[Dict[str, Any]]:
    return [
        b
        for b in page_records[page_no]["blocks"]
        if b["block_role"] == "caption" or re.search(r"^(图|表)\s*\d+\s*[:：]", b["text_normalized"])
    ]


def find_nearest_caption(
    page_records: Dict[int, Dict[str, Any]],
    page_no: int,
    bbox: Optional[Sequence[float]],
) -> Tuple[str, str]:
    captions = page_caption_blocks(page_records, page_no)
    if not captions:
        return "unknown", "unknown"
    if bbox is None:
        return one_line(captions[0]["text_normalized"]), "page_caption"
    x0, y0, x1, y1 = [float(v) for v in bbox]
    best: Optional[Tuple[float, Dict[str, Any], str]] = None
    for block in captions:
        bx0, by0, bx1, by1 = [float(v) for v in block["bbox"]]
        vertical = min(abs(by1 - y0), abs(by0 - y1))
        overlap = max(0.0, min(x1, bx1) - max(x0, bx0))
        source = "above_table" if by1 <= y0 else "below_table" if by0 >= y1 else "near_table"
        score = vertical - overlap / 1000.0
        if best is None or score < best[0]:
            best = (score, block, source)
    if best is None:
        return "unknown", "unknown"
    return one_line(best[1]["text_normalized"]), best[2]


def find_footnotes(page_records: Dict[int, Dict[str, Any]], page_no: int) -> List[str]:
    notes: List[str] = []
    for block in page_records[page_no]["blocks"]:
        text = one_line(block["text_normalized"])
        if text.startswith(("注：", "资料来源", "来源：")):
            notes.append(text)
    return notes


def source_blocks_for_bbox(
    page_records: Dict[int, Dict[str, Any]],
    page_no: int,
    bbox: Sequence[float],
) -> List[Dict[str, Any]]:
    x0, y0, x1, y1 = [float(v) for v in bbox]
    matched = []
    for block in page_records[page_no]["blocks"]:
        bx0, by0, bx1, by1 = [float(v) for v in block["bbox"]]
        horizontal = max(0.0, min(x1, bx1) - max(x0, bx0))
        vertical = max(0.0, min(y1, by1) - max(y0, by0))
        if horizontal > 0 and vertical > 0:
            matched.append(block)
    if matched:
        return matched
    captions = page_caption_blocks(page_records, page_no)
    return captions or page_records[page_no]["blocks"][:3]


def detect_units(text: str) -> str:
    unit_patterns = [
        r"单位[:：]\s*([^，。\n；;]+)",
        r"[（(]([^（）()]{1,20}元|%|百分比|亿元|千亿元|万亿元)[）)]",
    ]
    for pattern in unit_patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return "unknown"


def table_quality(rows: Sequence[Sequence[str]]) -> Tuple[float, str]:
    if not rows:
        return 0.0, "未提取到表格行"
    col_counts = [len(row) for row in rows if row]
    if not col_counts:
        return 0.0, "表格行列为空"
    common_cols = Counter(col_counts).most_common(1)[0][0]
    nonempty = sum(1 for row in rows for cell in row if str(cell or "").strip())
    total = sum(len(row) for row in rows)
    fill_ratio = nonempty / total if total else 0.0
    consistency = sum(1 for count in col_counts if count == common_cols) / len(col_counts)
    quality = round((fill_ratio * 0.55) + (consistency * 0.45), 2)
    note = f"行数={len(rows)}，常见列数={common_cols}，填充率={fill_ratio:.2f}，列数一致性={consistency:.2f}"
    return quality, note


def clean_rows(rows: Sequence[Sequence[Any]]) -> List[List[str]]:
    cleaned = []
    for row in rows:
        cleaned.append([one_line(str(cell or "")) for cell in row])
    return cleaned


def write_table_csv(path: Path, rows: Sequence[Sequence[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        for row in rows:
            writer.writerow(row)


def write_table_html(path: Path, rows: Sequence[Sequence[str]], caption: str) -> None:
    body = [f"<table>", f"<caption>{html.escape(caption)}</caption>"]
    for idx, row in enumerate(rows):
        tag = "th" if idx == 0 else "td"
        cells = "".join(f"<{tag}>{html.escape(str(cell))}</{tag}>" for cell in row)
        body.append(f"<tr>{cells}</tr>")
    body.append("</table>")
    path.write_text("\n".join(body), encoding="utf-8")


def extract_tables(
    doc: fitz.Document,
    doc_id: str,
    parsed_run: Path,
    project_root: Path,
    page_manifest: Dict[int, Dict[str, str]],
    page_records: Dict[int, Dict[str, Any]],
    evidence_chunks: List[Dict[str, Any]],
    unresolved: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    tables_dir = parsed_run / "tables"
    table_rows: List[Dict[str, Any]] = []
    table_pages = [p for p, row in page_manifest.items() if row.get("has_table_candidate") == "yes"]
    evidence_counter = Counter(chunk["content_type"] for chunk in evidence_chunks)
    for page_no in table_pages:
        page = doc.load_page(page_no - 1)
        try:
            found = page.find_tables().tables
        except Exception as exc:
            found = []
            unresolved.append(
                {
                    "item_id": f"unresolved_table_p{page_no:03d}",
                    "item_type": "table",
                    "page_no": page_no,
                    "reason": f"PyMuPDF表格检测异常：{exc}",
                    "severity": "high",
                    "recommended_action": "manual_table_review",
                    "related_output_path": relpath(tables_dir, project_root),
                }
            )
        if not found:
            evidence_counter["table_candidate_unparsed"] += 1
            evidence_chunks.append(
                {
                    "evidence_id": f"{doc_id}_p{page_no:03d}_table_candidate_unparsed_{evidence_counter['table_candidate_unparsed']:03d}",
                    "doc_id": doc_id,
                    "page_start": page_no,
                    "page_end": page_no,
                    "section_path": page_records[page_no].get("section_path", ["unknown_section"]),
                    "content_type": "table_candidate_unparsed",
                    "title_context": page_records[page_no].get("section_path", ["unknown"])[-1],
                    "text_raw": "",
                    "text_normalized": "表格候选页未能可靠提取表格结构，需人工复核原页面。",
                    "source_block_ids": [b["block_id"] for b in page_records[page_no]["blocks"][:3]],
                    "bbox_list": [b["bbox"] for b in page_records[page_no]["blocks"][:3]],
                    "source_parser": "pymupdf_find_tables",
                    "parse_quality": 0.0,
                    "stage3_priority": "medium",
                    "review_required": True,
                    "notes": "表格候选未解析，不包含产业结论。",
                }
            )
            unresolved.append(
                {
                    "item_id": f"unresolved_table_p{page_no:03d}",
                    "item_type": "table",
                    "page_no": page_no,
                    "reason": "表格候选页未检测到可输出的结构化表格",
                    "severity": "medium",
                    "recommended_action": "manual_table_review",
                    "related_output_path": relpath(tables_dir, project_root),
                }
            )
            continue
        for table_no, table in enumerate(found, start=1):
            table_id = f"{doc_id}_p{page_no:03d}_t{table_no:02d}"
            rows = clean_rows(table.extract() or [])
            quality, quality_note = table_quality(rows)
            bbox = [round(float(v), 2) for v in getattr(table, "bbox", [0, 0, 0, 0])]
            caption, caption_source = find_nearest_caption(page_records, page_no, bbox)
            page_text = "\n".join(b["text_normalized"] for b in page_records[page_no]["blocks"])
            units = detect_units(caption + "\n" + page_text)
            footnotes = find_footnotes(page_records, page_no)
            headers = rows[0] if rows else []
            review_required = (
                quality < 0.70
                or page_manifest[page_no].get("review_required") == "yes"
                or page_manifest[page_no].get("page_type") in {"mixed", "chain_diagram"}
            )
            csv_path = tables_dir / f"page_{page_no:03d}_table_{table_no:02d}.csv"
            html_path = tables_dir / f"page_{page_no:03d}_table_{table_no:02d}.html"
            json_path = tables_dir / f"page_{page_no:03d}_table_{table_no:02d}.json"
            write_table_csv(csv_path, rows)
            write_table_html(html_path, rows, caption)
            table_json = {
                "table_id": table_id,
                "doc_id": doc_id,
                "page_no": page_no,
                "table_no_on_page": table_no,
                "bbox": bbox,
                "caption": caption,
                "caption_source": caption_source,
                "headers": headers,
                "units": units,
                "footnotes": footnotes,
                "rows": rows,
                "extraction_method": "pymupdf_find_tables",
                "parse_quality": quality,
                "review_required": review_required,
                "source_page_image": relpath(
                    rendered_pages_dir(project_root, doc_id) / f"page_{page_no:03d}.png",
                    project_root,
                ),
            }
            write_json(json_path, table_json)
            table_rows.append(
                {
                    "table_id": table_id,
                    "page_no": page_no,
                    "caption": caption,
                    "headers_preview": " | ".join(headers[:6]),
                    "unit": units,
                    "footnote_present": "yes" if footnotes else "no",
                    "extraction_method": "pymupdf_find_tables",
                    "parse_quality": f"{quality:.2f}",
                    "csv_path": relpath(csv_path, project_root),
                    "html_path": relpath(html_path, project_root),
                    "json_path": relpath(json_path, project_root),
                    "review_required": "yes" if review_required else "no",
                    "quality_note": quality_note,
                }
            )
            if review_required:
                unresolved.append(
                    {
                        "item_id": f"review_table_p{page_no:03d}_t{table_no:02d}",
                        "item_type": "table",
                        "page_no": page_no,
                        "reason": f"表格结构需人工复核：{quality_note}",
                        "severity": "medium" if quality >= 0.55 else "high",
                        "recommended_action": "verify_table_structure",
                        "related_output_path": relpath(json_path, project_root),
                    }
                )
            evidence_counter["table"] += 1
            source_blocks = source_blocks_for_bbox(page_records, page_no, bbox)
            if not source_blocks:
                source_blocks = page_records[page_no].get("blocks", [])[:3]
            evidence_chunks.append(
                {
                    "evidence_id": f"{doc_id}_p{page_no:03d}_table_{evidence_counter['table']:03d}",
                    "doc_id": doc_id,
                    "page_start": page_no,
                    "page_end": page_no,
                    "section_path": page_records[page_no].get("section_path", ["unknown_section"]),
                    "content_type": "table",
                    "title_context": page_records[page_no].get("section_path", ["unknown"])[-1],
                    "text_raw": caption,
                    "text_normalized": (
                        f"表格证据：{caption}；表头预览：{' | '.join(headers[:8]) or 'unknown'}；"
                        f"单位：{units}；结构化文件：{relpath(json_path, project_root)}"
                    ),
                    "source_block_ids": [b["block_id"] for b in source_blocks],
                    "bbox_list": [bbox],
                    "source_parser": "pymupdf_find_tables",
                    "parse_quality": quality,
                    "stage3_priority": "high"
                    if page_manifest[page_no].get("has_chain_diagram_confirmed") == "yes"
                    else "medium",
                    "review_required": review_required,
                    "notes": "表格结构化证据，不包含产业关系推断。",
                }
            )
    write_csv(tables_dir / "table_manifest.csv", TABLE_MANIFEST_FIELDS, table_rows)
    return table_rows


def figure_captions_for_page(page_records: Dict[int, Dict[str, Any]], page_no: int) -> List[str]:
    captions = []
    for block in page_records[page_no]["blocks"]:
        text = one_line(block["text_normalized"])
        if re.match(r"^图\s*\d+\s*[:：]", text):
            captions.append(text)
    if not captions and page_records[page_no]["page_type"] in {"chart", "chain_diagram", "mixed", "table"}:
        caps = [one_line(b["text_normalized"]) for b in page_caption_blocks(page_records, page_no)]
        captions.extend(caps[:1])
    return captions or ["unknown"]


def copy_page_image(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.copy2(src, dst)
        return
    raise FileNotFoundError(f"Rendered page image not found: {src}")


def build_figures(
    doc_id: str,
    parsed_run: Path,
    project_root: Path,
    page_manifest: Dict[int, Dict[str, str]],
    page_records: Dict[int, Dict[str, Any]],
    evidence_chunks: Sequence[Dict[str, Any]],
    unresolved: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    figures_dir = parsed_run / "figures"
    rendered_dir = rendered_pages_dir(project_root, doc_id)
    evidence_by_page: Dict[int, List[str]] = defaultdict(list)
    for chunk in evidence_chunks:
        evidence_by_page[int(chunk["page_start"])].append(str(chunk["evidence_id"]))
    figure_rows: List[Dict[str, Any]] = []
    figure_pages = [
        p
        for p, row in page_manifest.items()
        if row.get("has_chart_candidate") == "yes" or row.get("has_chain_diagram_candidate") == "yes"
    ]
    for page_no in figure_pages:
        is_chain_candidate = page_manifest[page_no].get("has_chain_diagram_candidate") == "yes"
        is_chain_confirmed = page_manifest[page_no].get("has_chain_diagram_confirmed") == "yes"
        captions = figure_captions_for_page(page_records, page_no)
        src = rendered_dir / f"page_{page_no:03d}.png"
        for idx, caption in enumerate(captions, start=1):
            caption_chain_signal = bool(
                re.search(r"(产业链|价值链|供应链|主要环节|各环节|上游.*(中游|下游)|中游.*下游)", caption)
            )
            caption_is_chain = is_chain_confirmed and (
                caption_chain_signal or (caption == "unknown" and len(captions) == 1)
            )
            if caption_is_chain:
                image_name = f"page_{page_no:03d}_chain_diagram.png"
                figure_type = "chain_diagram"
            else:
                image_name = f"page_{page_no:03d}_figure_page.png"
                figure_type = "chart"
            dst = figures_dir / image_name
            if not dst.exists():
                copy_page_image(src, dst)
            figure_id = f"{doc_id}_p{page_no:03d}_fig{idx:02d}"
            row = {
                "figure_id": figure_id,
                "page_no": page_no,
                "figure_type": figure_type,
                "is_chain_diagram_candidate": "yes" if caption_is_chain or (is_chain_candidate and caption == "unknown") else "no",
                "is_chain_diagram_confirmed": "yes" if caption_is_chain else "no",
                "caption": caption,
                "caption_source": "caption_block" if caption != "unknown" else "unknown",
                "nearby_text_evidence_ids": ";".join(evidence_by_page.get(page_no, [])[:5]),
                "page_image_path": relpath(src, project_root),
                "figure_image_path": relpath(dst, project_root),
                "bbox": "null",
                "source_method": "rendered_page_copy_no_visual_inference",
                "review_required": "yes" if caption_is_chain or page_manifest[page_no].get("review_required") == "yes" else "no",
                "quality_note": "完整页面图归档，未识别节点、箭头或关系"
                if caption_is_chain
                else "普通图表页归档，未自动还原数据点",
            }
            figure_rows.append(row)
        if is_chain_confirmed:
            unresolved.append(
                {
                    "item_id": f"review_chain_diagram_p{page_no:03d}",
                    "item_type": "chain_diagram",
                    "page_no": page_no,
                    "reason": "产业链图仅归档为视觉证据，节点、箭头方向和关系需人工复核",
                    "severity": "high",
                    "recommended_action": "manual_visual_review_before_relation_extraction",
                    "related_output_path": relpath(dst, project_root),
                }
            )
    write_csv(figures_dir / "figure_manifest.csv", FIGURE_MANIFEST_FIELDS, figure_rows)
    return figure_rows


def add_general_unresolved(
    page_manifest: Dict[int, Dict[str, str]],
    page_records: Dict[int, Dict[str, Any]],
    unresolved: List[Dict[str, Any]],
    output_run: Path,
    project_root: Path,
) -> None:
    existing_ids = {item["item_id"] for item in unresolved}
    for page_no, manifest in page_manifest.items():
        if manifest.get("text_quality") in {"low", "garbled"}:
            item_id = f"review_text_quality_p{page_no:03d}"
            if item_id not in existing_ids:
                unresolved.append(
                    {
                        "item_id": item_id,
                        "item_type": "text",
                        "page_no": page_no,
                        "reason": f"第一阶段文本质量标记为 {manifest.get('text_quality')}",
                        "severity": "medium",
                        "recommended_action": "manual_text_order_and_quality_check",
                        "related_output_path": relpath(
                            output_run / "stage2_sample_evidence_review.md", project_root
                        ),
                    }
                )
        garbled_blocks = [
            block for block in page_records[page_no]["blocks"] if block.get("text_quality") == "garbled"
        ]
        if garbled_blocks:
            item_id = f"review_garbled_blocks_p{page_no:03d}"
            if item_id not in existing_ids:
                unresolved.append(
                    {
                        "item_id": item_id,
                        "item_type": "text",
                        "page_no": page_no,
                        "reason": f"检测到 {len(garbled_blocks)} 个疑似PDF字体映射乱码文本块",
                        "severity": "medium",
                        "recommended_action": "use_rendered_page_or_manual_review_for_garbled_text",
                        "related_output_path": relpath(
                            output_run / "stage2_sample_evidence_review.md", project_root
                        ),
                    }
                )
        if manifest.get("page_type") in {"mixed", "chart", "table"} and manifest.get("review_required") == "yes":
            item_id = f"review_layout_p{page_no:03d}"
            if item_id not in existing_ids:
                unresolved.append(
                    {
                        "item_id": item_id,
                        "item_type": "layout",
                        "page_no": page_no,
                        "reason": "图表/表格/混排页面，阅读顺序或结构需抽样核验",
                        "severity": "medium",
                        "recommended_action": "manual_layout_review",
                        "related_output_path": relpath(
                            output_run / "stage2_sample_evidence_review.md", project_root
                        ),
                    }
                )


def write_page_records(parsed_run: Path, page_records: Dict[int, Dict[str, Any]]) -> None:
    page_dir = parsed_run / "page_records"
    for page_no, record in page_records.items():
        write_json(page_dir / f"page_{page_no:03d}_blocks.json", record)


def sample_page_preview(page_records: Dict[int, Dict[str, Any]], page_no: int, limit: int = 800) -> str:
    text = "\n".join(
        b["text_normalized"]
        for b in page_records[page_no]["blocks"]
        if b["block_role"] not in {"header", "footer"}
    )
    return text[:limit] if text else "[无原生文本块]"


def write_sample_review(
    output_run: Path,
    project_root: Path,
    page_manifest: Dict[int, Dict[str, str]],
    page_records: Dict[int, Dict[str, Any]],
    evidence_chunks: Sequence[Dict[str, Any]],
    table_rows: Sequence[Dict[str, Any]],
    figure_rows: Sequence[Dict[str, Any]],
) -> None:
    available_pages = sorted(page_manifest)
    preferred_pages = [3, 8, 9, 12, 14, 22, available_pages[-1] if available_pages else 1]
    sample_pages = list(dict.fromkeys(p for p in preferred_pages if p in page_manifest))
    chunks_by_page = Counter(int(c["page_start"]) for c in evidence_chunks)
    tables_by_page = defaultdict(list)
    for row in table_rows:
        tables_by_page[int(row["page_no"])].append(row["table_id"])
    figures_by_page = defaultdict(list)
    for row in figure_rows:
        figures_by_page[int(row["page_no"])].append(row["figure_id"])
    lines = ["# Stage 2 抽样证据检查", ""]
    for page_no in sample_pages:
        manifest = page_manifest[page_no]
        page_file = project_root / "parsed_documents" / "latest_run.txt"
        lines.extend(
            [
                f"## Page {page_no:03d}",
                "",
                f"- 页面类型：{manifest['page_type']}",
                f"- 章节路径：{' > '.join(page_records[page_no].get('section_path', ['unknown_section']))}",
                f"- 正文证据块数量：{chunks_by_page.get(page_no, 0)}",
                f"- 表格处理：{', '.join(tables_by_page.get(page_no, [])) if tables_by_page.get(page_no) else '无成功表格或非表格页'}",
                f"- 图示处理：{', '.join(figures_by_page.get(page_no, [])) if figures_by_page.get(page_no) else '无图示归档'}",
                f"- 是否需人工复核：{manifest['review_required']}",
                f"- 页面记录：{relpath(project_root / 'parsed_documents' / 'latest_run.txt', project_root)} 指向本次 parsed run；具体文件为 page_records/page_{page_no:03d}_blocks.json",
                "",
                "```text",
                sample_page_preview(page_records, page_no),
                "```",
                "",
            ]
        )
    (output_run / "stage2_sample_evidence_review.md").write_text("\n".join(lines), encoding="utf-8")


def validate_outputs(
    pdf_hash_before: str,
    pdf_hash_after: str,
    parsed_run: Path,
    page_count: int,
    evidence_chunks: Sequence[Dict[str, Any]],
    table_rows: Sequence[Dict[str, Any]],
    figure_rows: Sequence[Dict[str, Any]],
    unresolved: Sequence[Dict[str, Any]],
    project_root: Path,
) -> Dict[str, Any]:
    evidence_ids = [chunk["evidence_id"] for chunk in evidence_chunks]
    source_ok = all(chunk.get("source_block_ids") for chunk in evidence_chunks)
    pages_ok = all(1 <= int(chunk["page_start"]) <= page_count and 1 <= int(chunk["page_end"]) <= page_count for chunk in evidence_chunks)
    page_files = [parsed_run / "page_records" / f"page_{i:03d}_blocks.json" for i in range(1, page_count + 1)]
    table_paths_ok = True
    for row in table_rows:
        for key in ["csv_path", "html_path", "json_path"]:
            if not (project_root / row[key]).exists():
                table_paths_ok = False
    unresolved_table_pages = {int(item["page_no"]) for item in unresolved if item["item_type"] == "table"}
    table_pages_with_rows = {int(row["page_no"]) for row in table_rows}
    chain_pages = {
        int(row["page_no"])
        for row in figure_rows
        if row["is_chain_diagram_confirmed"] == "yes"
    }
    return {
        "原始PDF_SHA256_运行前后相同": pdf_hash_before == pdf_hash_after,
        "document_structure_json存在": (parsed_run / "document_structure.json").exists(),
        "页面记录覆盖全文且无缺页": all(path.exists() for path in page_files),
        "evidence_chunks_jsonl非空": len(evidence_chunks) > 0,
        "每个evidence_id唯一": len(evidence_ids) == len(set(evidence_ids)),
        "每个证据块页码在范围内": pages_ok,
        "每个证据块能定位source_block_ids": source_ok,
        "表格manifest输出路径存在或已标记unresolved": table_paths_ok and bool(table_pages_with_rows or unresolved_table_pages),
        "已确认产业链图均有归档记录": all(
            row.get("figure_image_path") and (project_root / row["figure_image_path"]).exists()
            for row in figure_rows if row.get("is_chain_diagram_confirmed") == "yes"
        ),
        "未调用大模型": True,
        "未生成产业关系": True,
    }


def write_report(
    output_run: Path,
    parsed_run: Path,
    pdf_path: Path,
    doc_record: Dict[str, str],
    validation: Dict[str, Any],
    sections: Sequence[Dict[str, Any]],
    page_records: Dict[int, Dict[str, Any]],
    evidence_chunks: Sequence[Dict[str, Any]],
    table_rows: Sequence[Dict[str, Any]],
    figure_rows: Sequence[Dict[str, Any]],
    unresolved: Sequence[Dict[str, Any]],
    deps: Dict[str, str],
    log_path: Path,
) -> None:
    content_counts = Counter(str(chunk["content_type"]) for chunk in evidence_chunks)
    priority_counts = Counter(str(chunk["stage3_priority"]) for chunk in evidence_chunks)
    block_count = sum(len(record["blocks"]) for record in page_records.values())
    table_review_pages = sorted({int(row["page_no"]) for row in table_rows if row["review_required"] == "yes"})
    figure_count = len(figure_rows)
    chain_figure_pages = sorted(
        {int(row["page_no"]) for row in figure_rows if row["is_chain_diagram_confirmed"] == "yes"}
    )
    validation_lines = [f"- {key}: {value}" for key, value in validation.items()]
    content_lines = [f"- {k}: {v}" for k, v in sorted(content_counts.items())]
    deps_lines = [f"- {k}: {v}" for k, v in deps.items()]
    unresolved_lines = [
        f"- Page {item['page_no']} {item['item_type']}: {item['reason']} ({item['severity']})"
        for item in unresolved[:30]
    ] or ["- 无"]
    table_lines = [
        f"- Page {row['page_no']} {row['table_id']}: quality={row['parse_quality']}, review={row['review_required']}"
        for row in table_rows
    ] or ["- 无成功提取表格"]
    lines = [
        "# 第二阶段结构化证据库构建报告",
        "",
        "## 1. 文档与校验",
        "",
        f"- 文件：{pdf_path}",
        f"- doc_id：{doc_record.get('doc_id')}",
        f"- SHA256：{doc_record.get('file_sha256')}",
        f"- 页面总数：{doc_record.get('page_count')}",
        f"- parsed_run：{parsed_run}",
        f"- 日志：{log_path}",
        "",
        "## 2. 环境与依赖",
        "",
        *deps_lines,
        "",
        "## 3. 文本块与证据块",
        "",
        f"- 页面级文本块总数：{block_count}",
        f"- 证据块总数：{len(evidence_chunks)}",
        f"- 高优先级证据块数量：{priority_counts.get('high', 0)}",
        "",
        "### 证据块类型统计",
        "",
        *content_lines,
        "",
        "## 4. 章节识别",
        "",
        f"- 识别章节数：{len(sections)}",
        "- 局限：章节识别基于原生文本块、标题编号、字体和加粗估计；目录页不作为正文结构事实；少量图文混排页仍需人工核验阅读顺序。",
        "",
        "## 5. 表格处理",
        "",
        f"- 成功提取表格数：{len(table_rows)}",
        f"- 待复核表格页：{', '.join(map(str, table_review_pages)) if table_review_pages else '无'}",
        *table_lines,
        "",
        "## 6. 图表与产业链图",
        "",
        f"- 图表归档记录数：{figure_count}",
        f"- 产业链图归档：{', '.join('Page ' + str(page) for page in chain_figure_pages) if chain_figure_pages else '无'}",
        "- 已确认的产业链图仅作为待复核视觉证据，不自动识别节点、箭头、上下游关系或产业层级。",
        "",
        "## 7. 当前解析风险",
        "",
        "- 表格结构由 PyMuPDF 原生表格检测获得，复杂图表式表格仍需人工核验行列和表头。",
        "- 图表页只保留图题、周边文字和页面图，不自动还原全部数据点。",
        "- 产业链图仅归档为视觉证据，第三阶段前必须人工确认节点、箭头方向和文本边界。",
        "",
        "## 8. 第三阶段输入建议",
        "",
        "- 优先输入 evidence_chunks.jsonl 中 stage3_priority = high 的正文证据块。",
        "- 优先输入 tables\\ 中 parse_quality 较高且 review_required = false 的表格。",
        "- 已确认的产业链图仅作为待复核视觉证据，不可直接作为关系事实写入图谱。",
        "",
        "## 9. 待人工复核",
        "",
        *unresolved_lines,
        "",
        "## 11. 验证结果",
        "",
        *validation_lines,
        "",
        "## 12. 阶段边界声明",
        "",
        "- 本阶段未调用大模型、未做OCR、未抽取产业链关系、未生成产业链结论。",
        "",
    ]
    (output_run / "stage2_evidence_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    pdf_path = Path(args.pdf).resolve()
    log_path = setup_logging(project_root)
    logging.info("Stage 2 evidence build started")
    logging.info("Project root: %s", project_root)
    logging.info("PDF path: %s", pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    metadata_dir = project_root / "metadata"
    stage1_output_dir = project_root / "outputs" / "stage1_document_audit"
    documents = read_csv(metadata_dir / "documents.csv")
    page_manifest_rows = read_csv(metadata_dir / "page_manifest.csv")
    high_value_rows = read_csv(stage1_output_dir / "high_value_pages.csv")

    pdf_hash_before = sha256_file(pdf_path)
    doc_record = find_doc_record(documents, pdf_path, pdf_hash_before)
    doc_id = doc_record["doc_id"]
    document_profile = resolve_document_profile(
        project_root,
        hints=(pdf_path.name, doc_id, doc_record.get("title", "")),
        explicit=args.document_profile,
    )
    profile_rules = document_rules(document_profile)
    header_footer_markers = [str(x) for x in profile_rules.get("header_footer_markers", [])]
    logging.info("Document profile: %s", document_profile["profile_id"])
    if (doc_record.get("file_sha256") or "").lower() != pdf_hash_before.lower():
        raise RuntimeError("PDF SHA256 differs from documents.csv; refusing to build evidence store")

    page_manifest: Dict[int, Dict[str, str]] = {
        int(row["page_no"]): row for row in page_manifest_rows
    }
    high_priority: Dict[int, str] = {
        int(row["page_no"]): row.get("priority", "") for row in high_value_rows
    }

    doc = fitz.open(pdf_path)
    if doc.needs_pass or doc.is_encrypted:
        raise RuntimeError("PDF is encrypted or requires a password")
    page_count = doc.page_count
    if page_count != len(page_manifest):
        raise RuntimeError(f"Page count mismatch: PDF={page_count}, manifest={len(page_manifest)}")

    parsed_run, output_run = make_run_dirs(project_root, doc_id)
    logging.info("Parsed run dir: %s", parsed_run)
    logging.info("Output run dir: %s", output_run)

    page_records, all_blocks = extract_page_blocks(
        doc, page_manifest, doc_id, header_footer_markers
    )
    sections = build_sections_and_assign(page_records, all_blocks)
    document_structure = build_document_structure(
        doc_id, pdf_path, doc_record, page_manifest, page_records, sections, page_count
    )
    write_json(parsed_run / "document_structure.json", document_structure)
    write_page_records(parsed_run, page_records)

    evidence_chunks = build_evidence_chunks(doc_id, page_records, high_priority)
    unresolved: List[Dict[str, Any]] = []
    table_rows = extract_tables(
        doc,
        doc_id,
        parsed_run,
        project_root,
        page_manifest,
        page_records,
        evidence_chunks,
        unresolved,
    )
    figure_rows = build_figures(
        doc_id,
        parsed_run,
        project_root,
        page_manifest,
        page_records,
        evidence_chunks,
        unresolved,
    )
    add_general_unresolved(page_manifest, page_records, unresolved, output_run, project_root)
    append_jsonl(parsed_run / "evidence_chunks.jsonl", evidence_chunks)
    write_csv(output_run / "unresolved_items.csv", UNRESOLVED_FIELDS, unresolved)
    write_sample_review(
        output_run, project_root, page_manifest, page_records, evidence_chunks, table_rows, figure_rows
    )

    pdf_hash_after = sha256_file(pdf_path)
    validation = validate_outputs(
        pdf_hash_before,
        pdf_hash_after,
        parsed_run,
        page_count,
        evidence_chunks,
        table_rows,
        figure_rows,
        unresolved,
        project_root,
    )
    write_json(parsed_run / "validation_summary.json", validation)
    write_json(output_run / "validation_summary.json", validation)

    deps = {
        "Python": sys.version.split()[0],
        "PyMuPDF": getattr(fitz, "__version__", "unknown"),
        "Pillow": getattr(Image, "__version__", "unknown"),
        "pandas": "available" if module_available("pandas") else "missing",
        "pdfplumber": "available" if module_available("pdfplumber") else "missing",
        "BeautifulSoup4": "available" if module_available("bs4") else "missing",
    }
    write_report(
        output_run,
        parsed_run,
        pdf_path,
        doc_record,
        validation,
        sections,
        page_records,
        evidence_chunks,
        table_rows,
        figure_rows,
        unresolved,
        deps,
        log_path,
    )

    block_count = sum(len(record["blocks"]) for record in page_records.values())
    content_counts = Counter(chunk["content_type"] for chunk in evidence_chunks)
    high_count = sum(1 for chunk in evidence_chunks if chunk["stage3_priority"] == "high")
    unresolved_table_pages = sorted(
        {int(item["page_no"]) for item in unresolved if item["item_type"] == "table"}
    )
    print("")
    print("Stage 2 evidence build complete")
    print(f"doc_id: {doc_id}")
    print(f"sections: {len(sections)}")
    print(f"text blocks: {block_count}")
    print(f"evidence chunks: {len(evidence_chunks)} {dict(content_counts)}")
    print(f"tables extracted: {len(table_rows)}")
    print(f"table review pages: {unresolved_table_pages}")
    print(f"figures archived: {len(figure_rows)}")
    print(f"high-priority chunks: {high_count}")
    print(f"validation passed: {all(bool(v) for v in validation.values())}")
    print(f"parsed_run: {parsed_run}")
    print(f"output_run: {output_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
