#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OCR-repair Stage 2 evidence chunks that suffer from PDF font-map garbling.

This is an explicit post-processing step requested after the no-OCR Stage 2
baseline. It does not modify the original PDF or baseline run. It creates a new
parsed_documents run with OCR-derived evidence chunks for affected pages.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from PIL import Image
from document_profile_manager import document_rules, resolve_document_profile

try:
    from paddleocr import PaddleOCR
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PaddleOCR is required for OCR repair") from exc


BAD_TEXT_RE = re.compile(r"[\ufffd\u0400-\u04ff\u3400-\u4dbf\u2300-\u2bff]")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OCR repair evidence chunks")
    parser.add_argument("--project-root", required=True, help="Project root")
    parser.add_argument(
        "--parsed-run",
        default=None,
        help="Baseline parsed run. Defaults to parsed_documents/latest_run.txt",
    )
    parser.add_argument(
        "--ocr-pages",
        default="auto",
        help="Comma-separated page numbers, or auto to detect garbled pages",
    )
    parser.add_argument("--max-side", type=int, default=1600, help="Max OCR image side")
    parser.add_argument("--min-score", type=float, default=0.20, help="Minimum OCR line score to keep")
    parser.add_argument("--document-profile", default="auto", help="Document profile id or auto")
    return parser.parse_args()


def setup_logging(project_root: Path) -> Path:
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"stage2_ocr_repair_{stamp}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def relpath(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def contains_bad_text(value: str) -> bool:
    return bool(BAD_TEXT_RE.search(value or ""))


def clean_ocr_text(value: str) -> str:
    value = re.sub(r"[\r\f\v\t]+", " ", value or "")
    value = BAD_TEXT_RE.sub("", value)
    value = re.sub(r" {2,}", " ", value)
    return value.strip()


def get_baseline_run(project_root: Path, parsed_run_arg: str | None) -> Path:
    if parsed_run_arg:
        return Path(parsed_run_arg).resolve()
    latest = project_root / "parsed_documents" / "latest_run.txt"
    if not latest.exists():
        raise FileNotFoundError(f"Missing latest run pointer: {latest}")
    return Path(latest.read_text(encoding="utf-8").strip()).resolve()


def detect_ocr_pages(chunks: Sequence[Dict[str, Any]], page_count: int, ocr_pages_arg: str) -> List[int]:
    if ocr_pages_arg.lower() != "auto":
        pages = sorted({int(p.strip()) for p in ocr_pages_arg.split(",") if p.strip()})
        return [p for p in pages if 1 <= p <= page_count]
    pages = set()
    for chunk in chunks:
        page = int(chunk.get("page_start", 0))
        text = "\n".join(
            str(chunk.get(field, "")) for field in ["text_raw", "text_normalized", "notes"]
        )
        if page and contains_bad_text(text):
            pages.add(page)
    pages.discard(25)
    return sorted(pages)


def make_repair_run(project_root: Path, baseline_run: Path, doc_id: str) -> Tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parsed_run = project_root / "parsed_documents" / f"{doc_id}_ocr_repaired_{stamp}"
    output_run = project_root / "outputs" / "stage2_ocr_repair" / f"{doc_id}_{stamp}"
    if parsed_run.exists():
        raise FileExistsError(parsed_run)
    shutil.copytree(baseline_run, parsed_run)
    output_run.mkdir(parents=True, exist_ok=True)
    (project_root / "outputs" / "stage2_ocr_repair").mkdir(parents=True, exist_ok=True)
    (output_run / "latest_run.txt").write_text(
        f"parsed_run={parsed_run}\noutput_run={output_run}\n", encoding="utf-8"
    )
    return parsed_run, output_run


def create_ocr_image(src: Path, dst: Path, max_side: int) -> Tuple[int, int, int, int]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(src).convert("RGB")
    original_size = image.size
    image.thumbnail((max_side, max_side))
    image.save(dst)
    return original_size[0], original_size[1], image.size[0], image.size[1]


def to_list(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, list):
        return [to_list(v) for v in value]
    if isinstance(value, tuple):
        return [to_list(v) for v in value]
    return value


def bbox_from_any(box: Any) -> List[float]:
    data = to_list(box)
    if not data:
        return [0, 0, 0, 0]
    if len(data) == 4 and all(isinstance(v, (int, float)) for v in data):
        return [float(data[0]), float(data[1]), float(data[2]), float(data[3])]
    points = data
    xs = [float(p[0]) for p in points if len(p) >= 2]
    ys = [float(p[1]) for p in points if len(p) >= 2]
    return [min(xs), min(ys), max(xs), max(ys)] if xs and ys else [0, 0, 0, 0]


def run_page_ocr(
    ocr: PaddleOCR,
    image_path: Path,
    page_no: int,
    doc_id: str,
    min_score: float,
) -> List[Dict[str, Any]]:
    result = ocr.predict(str(image_path))
    if not result:
        return []
    first = result[0]
    texts = first.get("rec_texts") or []
    scores = first.get("rec_scores") or []
    boxes = first.get("rec_boxes")
    if boxes is None:
        boxes = first.get("rec_polys")
    if boxes is None:
        boxes = []
    lines: List[Dict[str, Any]] = []
    for idx, text in enumerate(texts):
        score = float(scores[idx]) if idx < len(scores) else 0.0
        cleaned = clean_ocr_text(str(text))
        if not cleaned or score < min_score:
            continue
        bbox = bbox_from_any(boxes[idx]) if idx < len(boxes) else [0, 0, 0, 0]
        lines.append(
            {
                "line_id": f"{doc_id}_p{page_no:03d}_ocr_l{idx + 1:03d}",
                "page_no": page_no,
                "reading_order": idx + 1,
                "text": cleaned,
                "score": round(score, 4),
                "bbox_ocr_image": [round(float(v), 2) for v in bbox],
            }
        )
    lines.sort(key=lambda row: (row["bbox_ocr_image"][1], row["bbox_ocr_image"][0]))
    for order, line in enumerate(lines, start=1):
        line["reading_order"] = order
    return lines


def group_ocr_lines(
    doc_id: str,
    page_no: int,
    lines: Sequence[Dict[str, Any]],
    page_priority: str,
    page_review_required: bool,
    max_chars: int = 1200,
) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []

    def flush() -> None:
        if not current:
            return
        idx = len(chunks) + 1
        text = "\n".join(line["text"] for line in current)
        avg_score = sum(line["score"] for line in current) / len(current)
        chunks.append(
            {
                "evidence_id": f"{doc_id}_p{page_no:03d}_ocr_{idx:03d}",
                "doc_id": doc_id,
                "page_start": page_no,
                "page_end": page_no,
                "section_path": ["ocr_repaired_page"],
                "content_type": "ocr_text",
                "title_context": "ocr_repaired_page",
                "text_raw": text,
                "text_normalized": text,
                "source_block_ids": [line["line_id"] for line in current],
                "bbox_list": [line["bbox_ocr_image"] for line in current],
                "source_parser": "paddleocr_ppocrv5_mobile",
                "parse_quality": round(avg_score, 3),
                "stage3_priority": page_priority,
                "review_required": bool(page_review_required or avg_score < 0.85),
                "notes": "OCR修复原生PDF字体映射乱码；bbox为OCR图像像素坐标。",
            }
        )
        current.clear()

    for line in lines:
        proposed_len = sum(len(item["text"]) for item in current) + len(line["text"])
        if current and proposed_len > max_chars:
            flush()
        current.append(line)
    flush()
    return chunks


def clean_table_reference_chunks(
    doc_id: str,
    project_root: Path,
    parsed_run: Path,
    ocr_pages: Sequence[int],
    high_pages: Dict[int, str],
) -> List[Dict[str, Any]]:
    manifest_path = parsed_run / "tables" / "table_manifest.csv"
    rows = read_csv(manifest_path)
    chunks = []
    counter = Counter()
    for row in rows:
        page_no = int(row["page_no"])
        if page_no not in ocr_pages:
            continue
        counter[page_no] += 1
        text = (
            f"表格结构化引用：page {page_no} table {counter[page_no]:02d}；"
            f"caption={clean_ocr_text(row.get('caption', 'unknown')) or 'unknown'}；"
            f"json_path={row.get('json_path', '')}；"
            f"parse_quality={row.get('parse_quality', '')}；review_required={row.get('review_required', '')}"
        )
        chunks.append(
            {
                "evidence_id": f"{doc_id}_p{page_no:03d}_table_ref_{counter[page_no]:03d}",
                "doc_id": doc_id,
                "page_start": page_no,
                "page_end": page_no,
                "section_path": ["ocr_repaired_page"],
                "content_type": "table_reference",
                "title_context": "ocr_repaired_page",
                "text_raw": text,
                "text_normalized": text,
                "source_block_ids": [row.get("table_id", "")],
                "bbox_list": [],
                "source_parser": "table_manifest_reference",
                "parse_quality": float(row.get("parse_quality") or 0.0),
                "stage3_priority": "high" if high_pages.get(page_no) == "high" else "medium",
                "review_required": True,
                "notes": "原生表格文本含字体映射风险，第三阶段应读取结构化表格文件并结合OCR页文本复核。",
            }
        )
    return chunks


def load_high_pages(project_root: Path) -> Dict[int, str]:
    rows = read_csv(project_root / "outputs" / "stage1_document_audit" / "high_value_pages.csv")
    return {int(row["page_no"]): row.get("priority", "") for row in rows}


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    log_path = setup_logging(project_root)
    baseline_run = get_baseline_run(project_root, args.parsed_run)
    structure = read_json(baseline_run / "document_structure.json")
    doc_id = structure["doc_id"]
    document_profile = resolve_document_profile(
        project_root,
        hints=(doc_id, structure.get("file_name", ""), structure.get("document_title", "")),
        explicit=args.document_profile,
    )
    profile_rules = document_rules(document_profile)
    profile_high_pages = {
        int(page_no) for page_no in profile_rules.get("ocr_high_priority_pages", [])
    }
    logging.info("Document profile: %s", document_profile["profile_id"])
    page_count = int(structure["page_count"])
    baseline_chunks = read_jsonl(baseline_run / "evidence_chunks.jsonl")
    ocr_pages = detect_ocr_pages(baseline_chunks, page_count, args.ocr_pages)
    high_pages = load_high_pages(project_root)

    if not ocr_pages:
        raise RuntimeError("No OCR pages detected. Use --ocr-pages to specify pages explicitly.")

    parsed_run, output_run = make_repair_run(project_root, baseline_run, doc_id)
    before_path = parsed_run / "evidence_chunks_before_ocr.jsonl"
    shutil.copy2(parsed_run / "evidence_chunks.jsonl", before_path)

    logging.info("Baseline run: %s", baseline_run)
    logging.info("OCR repair run: %s", parsed_run)
    logging.info("OCR pages: %s", ocr_pages)

    det_dir = Path(os.environ["USERPROFILE"]) / ".paddlex" / "official_models" / "PP-OCRv5_mobile_det"
    rec_dir = Path(os.environ["USERPROFILE"]) / ".paddlex" / "official_models" / "PP-OCRv5_mobile_rec"
    if not det_dir.exists() or not rec_dir.exists():
        raise FileNotFoundError("PaddleOCR local model cache not found.")
    ocr = PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_detection_model_dir=str(det_dir),
        text_recognition_model_name="PP-OCRv5_mobile_rec",
        text_recognition_model_dir=str(rec_dir),
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_det_limit_side_len=args.max_side,
    )

    page_manifest_rows = read_csv(project_root / "metadata" / "page_manifest.csv")
    page_manifest = {int(row["page_no"]): row for row in page_manifest_rows}
    doc_stem = doc_id.rsplit("_", 1)[0]
    rendered_dir = project_root / "data" / "rendered_pages" / doc_stem
    ocr_dir = parsed_run / "ocr_pages"
    manifest_rows: List[Dict[str, Any]] = []
    ocr_chunks: List[Dict[str, Any]] = []

    for page_no in ocr_pages:
        src_img = rendered_dir / f"page_{page_no:03d}.png"
        ocr_img = ocr_dir / f"page_{page_no:03d}_ocr_input.png"
        ow, oh, iw, ih = create_ocr_image(src_img, ocr_img, args.max_side)
        lines = run_page_ocr(ocr, ocr_img, page_no, doc_id, args.min_score)
        write_json(
            ocr_dir / f"page_{page_no:03d}_ocr_lines.json",
            {
                "doc_id": doc_id,
                "page_no": page_no,
                "source_image": relpath(src_img, project_root),
                "ocr_image": relpath(ocr_img, project_root),
                "source_image_size": [ow, oh],
                "ocr_image_size": [iw, ih],
                "ocr_engine": "PaddleOCR PP-OCRv5 mobile",
                "lines": lines,
            },
        )
        (ocr_dir / f"page_{page_no:03d}_ocr.txt").write_text(
            "\n".join(line["text"] for line in lines), encoding="utf-8"
        )
        avg_score = sum(line["score"] for line in lines) / len(lines) if lines else 0.0
        page_priority = (
            "high" if high_pages.get(page_no) == "high" or page_no in profile_high_pages else "medium"
        )
        page_review = page_manifest.get(page_no, {}).get("review_required") == "yes"
        new_chunks = group_ocr_lines(doc_id, page_no, lines, page_priority, page_review)
        ocr_chunks.extend(new_chunks)
        manifest_rows.append(
            {
                "page_no": page_no,
                "reason": "detected_font_map_garbled_text",
                "ocr_line_count": len(lines),
                "ocr_chunk_count": len(new_chunks),
                "avg_score": f"{avg_score:.3f}",
                "ocr_image_path": relpath(ocr_img, project_root),
                "ocr_json_path": relpath(ocr_dir / f"page_{page_no:03d}_ocr_lines.json", project_root),
                "ocr_txt_path": relpath(ocr_dir / f"page_{page_no:03d}_ocr.txt", project_root),
            }
        )

    retained_chunks = [
        chunk
        for chunk in baseline_chunks
        if int(chunk.get("page_start", 0)) not in set(ocr_pages)
    ]
    table_refs = clean_table_reference_chunks(doc_id, project_root, parsed_run, ocr_pages, high_pages)
    repaired_chunks = retained_chunks + ocr_chunks + table_refs
    repaired_chunks.sort(key=lambda row: (int(row["page_start"]), str(row["evidence_id"])))
    write_jsonl(parsed_run / "evidence_chunks.jsonl", repaired_chunks)

    write_csv(
        parsed_run / "ocr_repair_manifest.csv",
        [
            "page_no",
            "reason",
            "ocr_line_count",
            "ocr_chunk_count",
            "avg_score",
            "ocr_image_path",
            "ocr_json_path",
            "ocr_txt_path",
        ],
        manifest_rows,
    )
    write_csv(output_run / "ocr_repair_manifest.csv", list(manifest_rows[0].keys()), manifest_rows)

    bad_chunks = [
        chunk
        for chunk in repaired_chunks
        if contains_bad_text(str(chunk.get("text_raw", "")))
        or contains_bad_text(str(chunk.get("text_normalized", "")))
    ]
    validation = {
        "baseline_run": str(baseline_run),
        "ocr_repair_run": str(parsed_run),
        "ocr_pages": ocr_pages,
        "evidence_chunks_nonempty": len(repaired_chunks) > 0,
        "evidence_id_unique": len({c["evidence_id"] for c in repaired_chunks}) == len(repaired_chunks),
        "bad_special_symbol_chunks_after_ocr": len(bad_chunks),
        "bad_special_symbol_chunks_cleared": len(bad_chunks) == 0,
        "ocr_line_total": sum(int(row["ocr_line_count"]) for row in manifest_rows),
        "table_reference_chunks": len(table_refs),
        "original_pdf_not_modified": True,
    }
    write_json(parsed_run / "ocr_repair_validation.json", validation)
    write_json(output_run / "ocr_repair_validation.json", validation)

    report = [
        "# OCR 修复报告",
        "",
        f"- baseline_run: {baseline_run}",
        f"- ocr_repair_run: {parsed_run}",
        f"- OCR 页码: {', '.join(map(str, ocr_pages))}",
        f"- OCR 行数: {validation['ocr_line_total']}",
        f"- 新 evidence_chunks 数: {len(repaired_chunks)}",
        f"- OCR 后特殊符号块数: {len(bad_chunks)}",
        f"- 表格引用块数: {len(table_refs)}",
        "",
        "说明：本次修复用 PaddleOCR PP-OCRv5 mobile 对字体映射乱码页整页 OCR，"
        "并在新的 evidence_chunks.jsonl 中用 OCR 证据块替换这些页面的原生文本块。"
        "原生版本保留为 evidence_chunks_before_ocr.jsonl。",
        "",
    ]
    (output_run / "ocr_repair_report.md").write_text("\n".join(report), encoding="utf-8")
    (project_root / "parsed_documents" / "latest_run.txt").write_text(str(parsed_run), encoding="utf-8")
    (project_root / "outputs" / "stage2_ocr_repair" / "latest_run.txt").write_text(
        str(output_run), encoding="utf-8"
    )

    print("")
    print("OCR repair complete")
    print(f"baseline_run: {baseline_run}")
    print(f"ocr_repair_run: {parsed_run}")
    print(f"ocr_pages: {ocr_pages}")
    print(f"ocr_lines: {validation['ocr_line_total']}")
    print(f"evidence_chunks: {len(repaired_chunks)}")
    print(f"bad_special_symbol_chunks_after_ocr: {len(bad_chunks)}")
    print(f"output_run: {output_run}")
    print(f"log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
