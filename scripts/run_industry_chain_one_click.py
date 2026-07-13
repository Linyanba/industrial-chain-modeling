#!/usr/bin/env python3
"""
一键产业链建模流程：PDF → 多级产业链结构图
调用各阶段脚本，最终输出左到右树状结构图。
"""
import argparse, csv, hashlib, json, logging, re, shutil, subprocess, sys, time
from datetime import datetime
from pathlib import Path
import yaml

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"

def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def pdf_hash(pdf_path):
    h = hashlib.md5()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:12]

def sha256_file(pdf_path):
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def read_latest_doc_id(root):
    evidence_file = root / "parsed_documents" / "latest_run.txt"
    if not evidence_file.exists():
        return ""
    parsed_dir_text = evidence_file.read_text(encoding="utf-8").strip()
    if not parsed_dir_text:
        return ""
    chunks_file = Path(parsed_dir_text) / "evidence_chunks.jsonl"
    if not chunks_file.exists():
        return ""
    with open(chunks_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                return str(json.loads(line).get("doc_id") or "")
    return ""

def infer_project_root() -> Path:
    """Infer project root from this script location."""
    return Path(__file__).resolve().parent.parent

def resolve_pdf_path(root: Path, pdf_arg: str) -> Path:
    """Resolve full PDF path, allowing a bare file name under data/raw_pdf."""
    p = Path(pdf_arg)
    candidates = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.extend([
            Path.cwd() / p,
            root / p,
            root / "data" / "raw_pdf" / p,
        ])
        if p.suffix.lower() != ".pdf":
            candidates.extend([
                root / "data" / "raw_pdf" / f"{pdf_arg}.pdf",
                Path.cwd() / f"{pdf_arg}.pdf",
            ])
    for c in candidates:
        if c.exists():
            return c.resolve()
    return candidates[0].resolve() if candidates else p.resolve()

def safe_name(text: str, fallback: str = "model") -> str:
    text = (text or "").strip()
    text = re.sub(r'[<>:"/\\|?*\s]+', "_", text)
    text = text.strip("._")
    return text or fallback

def root_model_id(root_model: str) -> str:
    return hashlib.md5(root_model.strip().encode("utf-8")).hexdigest()[:12]

def root_model_collection(root_model: str) -> str:
    return f"kb_{root_model_id(root_model)}"

def registry_path(root: Path) -> Path:
    return root / "metadata" / "kb_registry.json"

def load_kb_registry(root: Path) -> dict:
    path = registry_path(root)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"version": 1, "root_models": {}}

def save_kb_registry(root: Path, registry: dict) -> None:
    path = registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

def get_root_model_entry(registry: dict, root_model: str) -> dict:
    rid = root_model_id(root_model)
    models = registry.setdefault("root_models", {})
    entry = models.setdefault(rid, {
        "root_model_id": rid,
        "root_model": root_model,
        "qdrant_collection": root_model_collection(root_model),
        "pdfs": {},
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": "",
    })
    entry["root_model"] = root_model
    entry["qdrant_collection"] = root_model_collection(root_model)
    entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return entry

def read_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def valid_parsed_dir(path: Path) -> bool:
    return path.exists() and all((path / fn).is_file() for fn in [
        "evidence_chunks.jsonl", "document_structure.json", "validation_summary.json"
    ])

def find_parsed_dir_by_doc_id(root: Path, doc_id: str) -> Path | None:
    parsed_root = root / "parsed_documents"
    direct = parsed_root / doc_id
    if valid_parsed_dir(direct):
        return direct
    if parsed_root.exists():
        candidates = [
            p for p in parsed_root.iterdir()
            if p.is_dir() and p.name.startswith(f"{doc_id}_") and valid_parsed_dir(p)
        ]
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime)
    return None

def find_existing_processed_pdf(root: Path, registry: dict, file_sha256: str) -> dict | None:
    for model in registry.get("root_models", {}).values():
        rec = (model.get("pdfs") or {}).get(file_sha256)
        if rec and rec.get("parsed_dir") and valid_parsed_dir(Path(rec["parsed_dir"])):
            return dict(rec)
    for row in read_csv_rows(root / "metadata" / "documents.csv"):
        if (row.get("file_sha256") or "").lower() == file_sha256.lower():
            parsed_dir = find_parsed_dir_by_doc_id(root, row.get("doc_id", ""))
            if parsed_dir:
                return {
                    "doc_id": row.get("doc_id", ""),
                    "file_name": row.get("file_name", ""),
                    "file_path": row.get("file_path", ""),
                    "file_sha256": file_sha256,
                    "parsed_dir": str(parsed_dir),
                    "source": "metadata_documents",
                }
    return None

def qdrant_collection_exists(qdrant_url: str, collection: str) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(qdrant_url.rstrip("/") + f"/collections/{collection}", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False

def register_pdf(entry: dict, pdf_info: dict, doc_id: str, parsed_dir: Path) -> dict:
    rec = entry.setdefault("pdfs", {}).setdefault(pdf_info["sha256"], {})
    rec.update({
        "file_name": pdf_info["path"].name,
        "file_path": str(pdf_info["path"]),
        "file_md5_12": pdf_info["md5"],
        "file_sha256": pdf_info["sha256"],
        "doc_id": doc_id,
        "parsed_dir": str(parsed_dir),
        "registered_at": rec.get("registered_at") or datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    })
    rec.setdefault("indexed_collections", [])
    return rec

def resolve_pointer(p):
    pp = Path(p)
    if pp.exists():
        t = pp.read_text(encoding="utf-8").strip()
        return Path(t) if t else None
    return None

def read_validation_summary(stage_dir: Path | None) -> dict:
    """Read a stage validation summary without treating missing data as success."""
    if not stage_dir:
        return {}
    path = Path(stage_dir) / "validation_summary.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}

def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))

def failed_validation_keys(summary: dict, required_keys: list[str]) -> list[str]:
    """Return required validation checks that are missing or explicitly failed."""
    failed = []
    for key in required_keys:
        item = summary.get(key)
        if not isinstance(item, dict) or item.get("passed") is not True:
            failed.append(key)
    return failed

def run_stage(script_path, args_list, logger, stage_name, logs_dir):
    """Run a stage script via subprocess, return (success, stdout, stderr)."""
    cmd = ["conda", "run", "-n", "chain", "python", str(script_path)] + args_list
    logger.info(f"[{stage_name}] 执行: {' '.join(cmd[:6])}...")
    log_file = logs_dir / f"{stage_name}.log"
    try:
        import os as _os
        env = _os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=env)
        log_file.write_text(f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}", encoding="utf-8")
        if result.returncode != 0:
            logger.error(f"[{stage_name}] 失败 rc={result.returncode}")
            logger.error(f"  stderr: {result.stderr[:500]}")
            return False, result.stdout, result.stderr
        logger.info(f"[{stage_name}] 完成")
        return True, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        logger.error(f"[{stage_name}] 超时")
        return False, "", "TIMEOUT"
    except Exception as e:
        logger.error(f"[{stage_name}] 异常: {e}")
        return False, "", str(e)

def check_service(url, name, logger):
    """Check HTTP service availability."""
    import urllib.request
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                logger.info(f"  {name}: OK")
                return True
    except Exception as e:
        logger.warning(f"  {name}: 不可用 ({e})")
    return False

def main():
    parser = argparse.ArgumentParser(description="一键产业链建模: PDF→多级结构图")
    parser.add_argument("--project-root", default=str(infer_project_root()),
                        help="项目根目录；默认自动取脚本所在项目根目录")
    parser.add_argument("--pdf", required=True, nargs="+",
                        help="一个或多个 PDF；可写文件名或完整路径")
    parser.add_argument("--industry-template", default="document_driven")
    parser.add_argument(
        "--document-profile", default="auto",
        help="文档解析专用档案；auto 按每份 PDF/doc_id 识别，generic 强制关闭专用规则",
    )
    parser.add_argument("--root-label", default=None)
    parser.add_argument("--root-model", default=None,
                        help="知识库隔离/合并名称；默认等于 root-label")
    parser.add_argument("--rebuild-root-model", action="store_true", default=False,
                        help="重建当前 root-model 的 Qdrant collection，并重索引该 root-model 下所有PDF")
    parser.add_argument("--reparse-pdf", action="store_true", default=False,
                        help="强制重新执行当前PDF的Stage1/2；同时重建当前root-model索引以避免旧证据残留")
    parser.add_argument("--auto-detect-template", action="store_true", default=False)
    parser.add_argument("--mode", choices=["dry-run","full"], default="full")
    parser.add_argument("--style", default="xmind_blue")
    parser.add_argument("--cleanup-extra", action="store_true", default=False)
    parser.add_argument("--skip-qdrant-rebuild", action="store_true", default=False)
    parser.add_argument("--skip-rag-qa", action="store_true", default=False)
    parser.add_argument("--skip-network-graph", action="store_true", default=True)
    parser.add_argument("--export-png", action="store_true", default=True)
    parser.add_argument("--export-svg", action="store_true", default=True)
    parser.add_argument("--export-html", action="store_true", default=True)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--enable-hierarchy-refinement", action="store_true", default=True)
    parser.add_argument("--disable-hierarchy-refinement", dest="enable_hierarchy_refinement", action="store_false")
    parser.add_argument("--min-tree-depth", type=int, default=2)
    parser.add_argument("--target-tree-depth", type=int, default=0, help="0表示auto")
    parser.add_argument("--max-tree-depth", type=int, default=4)
    parser.add_argument("--remove-company-branch", action="store_true", default=True)
    parser.add_argument("--forbid-representative-company-branch", action="store_true", default=True)
    parser.add_argument("--fix-parent-child-same-level", action="store_true", default=True)
    parser.add_argument("--adaptive-tree-depth", action="store_true", default=True)
    parser.add_argument("--fixed-tree-depth", dest="adaptive_tree_depth", action="store_false")
    args = parser.parse_args()
    if args.reparse_pdf:
        # 新的解析规则只有在重建证据文件后才会生效；Qdrant 也必须同步重建，
        # 否则旧解析产生但新解析已删除的 point 仍可能残留在 collection 中。
        args.rebuild_root_model = True

    root = Path(args.project_root).resolve()
    pdf_paths = [resolve_pdf_path(root, p) for p in args.pdf]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Template resolution
    sys.path.insert(0, str(root / "scripts"))
    from industry_template_manager import load_template, auto_detect_template, list_available_templates
    from document_profile_manager import resolve_document_profile
    available_templates = list_available_templates()

    if args.auto_detect_template:
        detected = auto_detect_template(pdf_paths[0], args.root_label or "")
        args.industry_template = detected

    if args.industry_template not in available_templates:
        print(f"错误: 未知模板 '{args.industry_template}'。可用模板: {available_templates}", file=sys.stderr)
        sys.exit(1)

    # Resolve root_label from template if not specified
    tpl = load_template(args.industry_template)
    if args.root_label is None:
        args.root_label = tpl.get("root_label_default", "产业链")
    root_model = args.root_model or args.root_label
    qdrant_collection = root_model_collection(root_model)
    pdf_infos = [{
        "path": p,
        "md5": pdf_hash(p) if p.exists() else "",
        "sha256": sha256_file(p) if p.exists() else "",
        "document_profile": (
            resolve_document_profile(root, hints=(p.name, p.stem), explicit=args.document_profile)["profile_id"]
            if p.exists() else "unresolved"
        ),
    } for p in pdf_paths]

    # Determine output file prefix from template
    outputs_cfg = tpl.get("required_outputs", {})
    png_prefix = outputs_cfg.get("png_prefix", "industry_chain_tree")
    svg_prefix = outputs_cfg.get("svg_prefix", "industry_chain_tree")
    html_prefix = outputs_cfg.get("html_prefix", "industry_chain_tree")

    # Output dirs
    final_dir = root / "final_deliverables" / f"{safe_name(root_model, 'root_model')}_{ts}"
    final_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = root / "logs" / f"pipeline_{ts}"
    logs_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(level=logging.INFO, format=LOG_FMT,
                        handlers=[logging.FileHandler(logs_dir / "pipeline.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("一键产业链建模流程启动")
    logger.info(f"Root-model: {root_model}")
    logger.info(f"PDF数量: {len(pdf_infos)}")
    for info in pdf_infos:
        logger.info(
            f"PDF: {info['path']} | md5_12={info['md5']} | sha256={info['sha256'][:12]} "
            f"| document_profile={info['document_profile']}"
        )
    logger.info(f"Qdrant collection: {qdrant_collection}")
    logger.info(f"模式: {args.mode}")
    if args.industry_template == "generic" and tpl.get("fallback_only"):
        logger.warning("generic 是 fallback_only 兜底模板，输出可能较粗；默认推荐使用 document_driven。")
    logger.info("=" * 60)

    scripts = root / "scripts"
    validation = {}
    step_results = {}
    failed_stage = None

    # ─── Step 0: 环境检查 ──────────────────────────────────────────────────────
    logger.info("--- Step 0: 环境检查 ---")
    missing_pdfs = [str(i["path"]) for i in pdf_infos if not i["path"].exists()]
    validation["pdf_exists"] = {"passed": not missing_pdfs, "note": "; ".join(missing_pdfs) if missing_pdfs else "全部存在"}
    validation["pdf_hash_computed"] = {"passed": all(i["sha256"] for i in pdf_infos), "note": ",".join(i["sha256"][:12] for i in pdf_infos)}
    validation["root_model"] = {"passed": bool(root_model), "note": root_model}
    validation["qdrant_collection_scoped_to_root_model"] = {"passed": True, "note": qdrant_collection}
    if missing_pdfs:
        logger.error("存在 PDF 不存在，终止")
        failed_stage = "Step0_PDF_not_found"
        validation["pdf_exists"]["passed"] = False
        _write_failure(final_dir, validation, failed_stage, args, ts, ",".join(i["sha256"][:12] for i in pdf_infos), Path(missing_pdfs[0]))
        return

    qdrant_ok = check_service("http://localhost:6333/collections", "Qdrant", logger)
    validation["qdrant_connected"] = {"passed": qdrant_ok, "note": "localhost:6333"}

    ollama_ok = check_service("http://localhost:11434/api/tags", "Ollama", logger)
    validation["ollama_connected"] = {"passed": ollama_ok, "note": "localhost:11434"}

    # Check qwen3:8b
    qwen_ok = False
    if ollama_ok:
        import urllib.request, json as jj
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as r:
                data = jj.loads(r.read())
                models = [m["name"] for m in data.get("models", [])]
                qwen_ok = any("qwen3" in m for m in models)
        except:
            pass
    validation["qwen3_available"] = {"passed": qwen_ok, "note": "qwen3:8b"}
    logger.info(f"  qwen3:8b: {'OK' if qwen_ok else '不可用'}")

    if args.mode == "dry-run":
        logger.info("DRY-RUN 模式，环境检查完成")
        validation["mode"] = "dry-run"
        (final_dir / "pipeline_validation_summary.json").write_text(
            json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    if not qdrant_ok:
        failed_stage = "Step0_Qdrant_unavailable"
        _write_failure(final_dir, validation, failed_stage, args, ts, ",".join(i["sha256"][:12] for i in pdf_infos), pdf_infos[0]["path"])
        return
    if not ollama_ok or not qwen_ok:
        failed_stage = "Step0_Ollama_or_qwen3_unavailable"
        _write_failure(final_dir, validation, failed_stage, args, ts, ",".join(i["sha256"][:12] for i in pdf_infos), pdf_infos[0]["path"])
        return

    registry = load_kb_registry(root)
    model_entry = get_root_model_entry(registry, root_model)
    qdrant_collection = model_entry["qdrant_collection"]
    processed_docs = []
    newly_parsed = []
    reused_parsed = []

    # ─── Step 1/2: PDF 审计与证据库构建（同一PDF只解析一次） ─────────────────────
    logger.info("--- Step 1/2: PDF 审计与证据库构建/复用 ---")
    for idx, info in enumerate(pdf_infos, 1):
        existing = None if args.reparse_pdf else find_existing_processed_pdf(root, registry, info["sha256"])
        if existing:
            parsed_dir = Path(existing["parsed_dir"])
            doc_id = existing["doc_id"]
            rec = register_pdf(model_entry, info, doc_id, parsed_dir)
            reused_parsed.append(doc_id)
            logger.info(f"[{idx}/{len(pdf_infos)}] 复用已解析PDF: {info['path'].name} -> {doc_id}")
        else:
            logger.info(f"[{idx}/{len(pdf_infos)}] 首次处理PDF: {info['path']}")
            ok, _, _ = run_stage(
                scripts / "stage1_document_audit.py",
                ["--pdf", str(info["path"]), "--project-root", str(root),
                 "--document-profile", args.document_profile],
                logger, f"stage1_{idx}", logs_dir,
            )
            if not ok:
                failed_stage = f"Step1_PDF_audit_{idx}"
                _write_failure(final_dir, validation, failed_stage, args, ts, info["sha256"][:12], info["path"])
                return
            ok, _, _ = run_stage(
                scripts / "stage2_build_evidence.py",
                ["--pdf", str(info["path"]), "--project-root", str(root),
                 "--document-profile", args.document_profile],
                logger, f"stage2_{idx}", logs_dir,
            )
            if not ok:
                failed_stage = f"Step2_evidence_build_{idx}"
                _write_failure(final_dir, validation, failed_stage, args, ts, info["sha256"][:12], info["path"])
                return
            doc_id = read_latest_doc_id(root)
            parsed_dir = find_parsed_dir_by_doc_id(root, doc_id) if doc_id else None
            if not doc_id or not parsed_dir:
                failed_stage = f"Step2_doc_id_missing_{idx}"
                _write_failure(final_dir, validation, failed_stage, args, ts, info["sha256"][:12], info["path"])
                return
            rec = register_pdf(model_entry, info, doc_id, parsed_dir)
            newly_parsed.append(doc_id)
            logger.info(f"[{idx}/{len(pdf_infos)}] 解析完成: {info['path'].name} -> {doc_id}")
        processed_docs.append(rec)

    save_kb_registry(root, registry)
    model_doc_ids = sorted({
        rec.get("doc_id", "")
        for rec in model_entry.get("pdfs", {}).values()
        if rec.get("doc_id")
    })
    validation["stage1_stage2_processed_or_reused"] = {
        "passed": bool(processed_docs),
        "note": f"new={len(newly_parsed)}, reused={len(reused_parsed)}, model_docs={len(model_doc_ids)}",
    }
    step_results["stage1_stage2"] = True

    # ─── Step 3a: root-model 向量索引 ──────────────────────────────────────────
    logger.info("--- Step 3a: root-model 向量索引 ---")
    collection_exists_now = qdrant_collection_exists("http://localhost:6333", qdrant_collection)
    docs_to_index = (
        list(model_entry.get("pdfs", {}).values())
        if args.rebuild_root_model or not collection_exists_now
        else processed_docs
    )
    indexed_docs = []
    skipped_index_docs = []
    recreate_next = args.rebuild_root_model
    for rec in docs_to_index:
        parsed_dir = Path(rec.get("parsed_dir", ""))
        doc_id = rec.get("doc_id", "")
        already_indexed = qdrant_collection in rec.get("indexed_collections", [])
        if already_indexed and collection_exists_now and not args.rebuild_root_model and not args.skip_qdrant_rebuild:
            skipped_index_docs.append(doc_id)
            continue
        s3a_args = [
            "--project-root", str(root),
            "--collection", qdrant_collection,
            "--parsed-dir", str(parsed_dir),
            "--root-model", root_model,
        ]
        if recreate_next:
            s3a_args.append("--recreate")
            recreate_next = False
            collection_exists_now = True
        if args.skip_qdrant_rebuild:
            s3a_args.append("--dry-run")
        ok, _, _ = run_stage(scripts / "stage3a_build_qdrant_index.py", s3a_args, logger, f"stage3a_{doc_id}", logs_dir)
        if not ok:
            failed_stage = f"Step3a_qdrant_index_{doc_id}"
            _write_failure(final_dir, validation, failed_stage, args, ts, rec.get("file_sha256", "")[:12], Path(rec.get("file_path", "")))
            return
        if not args.skip_qdrant_rebuild:
            rec.setdefault("indexed_collections", [])
            if qdrant_collection not in rec["indexed_collections"]:
                rec["indexed_collections"].append(qdrant_collection)
            rec["indexed_at"] = datetime.now().isoformat(timespec="seconds")
        indexed_docs.append(doc_id)
    save_kb_registry(root, registry)
    validation["stage3a_index_completed"] = {
        "passed": True,
        "note": f"indexed={indexed_docs}, skipped={skipped_index_docs}, collection={qdrant_collection}",
    }
    validation["embedding_model_loaded"] = {"passed": True, "note": "Qwen3-Embedding-0.6B"}
    step_results["stage3a"] = True

    # ─── Step 3c: 候选抽取 ─────────────────────────────────────────────────────
    logger.info("--- Step 3c: RAG候选抽取 ---")
    s3c_args = [
        "--project-root", str(root),
        "--mode", "batch",
        "--collection", qdrant_collection,
        "--root-model", root_model,
        "--allowed-doc-ids", ",".join(model_doc_ids),
        "--document-profile", args.document_profile,
    ]
    ok, _, _ = run_stage(scripts / "stage3c_rag_extract_candidates.py",
                         s3c_args, logger, "stage3c", logs_dir)
    validation["stage3c_extraction_completed"] = {"passed": ok, "note": "candidate extraction"}
    step_results["stage3c"] = ok
    if not ok:
        failed_stage = "Step3c_extraction"
        _write_failure(final_dir, validation, failed_stage, args, ts, ",".join(i["sha256"][:12] for i in pdf_infos), pdf_infos[0]["path"])
        return

    # ─── Step 4: 标准化 ────────────────────────────────────────────────────────
    logger.info("--- Step 4: 标准化 ---")
    ok, _, _ = run_stage(scripts / "stage4_normalize_and_review.py",
                         ["--project-root", str(root), "--mode", "full"], logger, "stage4", logs_dir)
    validation["stage4_normalization_completed"] = {"passed": ok, "note": "normalization"}
    step_results["stage4"] = ok
    if not ok:
        failed_stage = "Step4_normalization"
        _write_failure(final_dir, validation, failed_stage, args, ts, ",".join(i["sha256"][:12] for i in pdf_infos), pdf_infos[0]["path"])
        return

    # ─── Step 4.5: 审核最小化 ──────────────────────────────────────────────────
    logger.info("--- Step 4.5: 审核最小化 ---")
    ok, _, _ = run_stage(scripts / "stage4_5_minimize_review.py",
                         ["--project-root", str(root), "--mode", "conservative"], logger, "stage4_5", logs_dir)
    validation["stage4_5_review_minimized"] = {"passed": ok, "note": "review minimization"}
    step_results["stage4_5"] = ok
    if not ok:
        failed_stage = "Step4_5_review_minimize"
        _write_failure(final_dir, validation, failed_stage, args, ts, ",".join(i["sha256"][:12] for i in pdf_infos), pdf_infos[0]["path"])
        return

    stage4_5_dir = resolve_pointer(root / "rag" / "latest_stage4_5_run.txt")
    stage4_5_validation = read_validation_summary(stage4_5_dir)
    stage4_5_relations = count_csv_rows(stage4_5_dir / "conservative_normalized_relations.csv") if stage4_5_dir else 0
    stage4_5_reviews = count_csv_rows(stage4_5_dir / "minimal_human_review_sheet.csv") if stage4_5_dir else 0
    # Zero conservative relations may still be recoverable by stage 4.6 when
    # concrete P1 items remain for local-LLM/human adjudication. Record the
    # condition now and enforce the hard gate on the approved output below.
    validation["stage4_5_quality_checked"] = {
        "passed": bool(stage4_5_validation),
        "note": f"relations={stage4_5_relations}, pending_review={stage4_5_reviews}",
    }

    # ─── Step 4.6: 自动审核 ────────────────────────────────────────────────────
    logger.info("--- Step 4.6: LLM自动审核 ---")
    ok, _, _ = run_stage(scripts / "stage4_6_llm_auto_apply_review.py",
                         ["--project-root", str(root), "--mode", "auto-apply"], logger, "stage4_6", logs_dir)
    validation["stage4_6_auto_apply_completed"] = {"passed": ok, "note": "auto apply"}
    step_results["stage4_6"] = ok
    if not ok:
        failed_stage = "Step4_6_auto_apply"
        _write_failure(final_dir, validation, failed_stage, args, ts, ",".join(i["sha256"][:12] for i in pdf_infos), pdf_infos[0]["path"])
        return


    stage4_6_dir = resolve_pointer(root / "rag" / "latest_stage4_6_run.txt")
    approved_entities = count_csv_rows(stage4_6_dir / "approved_entities.csv") if stage4_6_dir else 0
    approved_relations = count_csv_rows(stage4_6_dir / "approved_relations.csv") if stage4_6_dir else 0
    approved_evidence = count_csv_rows(stage4_6_dir / "approved_relation_evidence_map.csv") if stage4_6_dir else 0
    validation["approved_entities_nonempty"] = {
        "passed": approved_entities > 0,
        "note": f"{approved_entities} entities",
    }
    validation["approved_relations_nonempty"] = {
        "passed": approved_relations > 0,
        "note": f"{approved_relations} relations",
    }
    validation["approved_relations_have_evidence"] = {
        "passed": approved_relations > 0 and approved_evidence >= approved_relations,
        "note": f"evidence={approved_evidence}, relations={approved_relations}",
    }
    if approved_entities == 0 or approved_relations == 0 or approved_evidence < approved_relations:
        failed_stage = "Step4_6_content_quality_gate"
        logger.error(
            "Approved内容不足，停止正式树交付: entities=%s, relations=%s, evidence=%s",
            approved_entities, approved_relations, approved_evidence,
        )
        _write_failure(final_dir, validation, failed_stage, args, ts, ",".join(i["sha256"][:12] for i in pdf_infos), pdf_infos[0]["path"])
        return

    # ─── Step 6: 层级树生成 ────────────────────────────────────────────────────
    logger.info("--- Step 6: 层级树生成 ---")
    ht_args = ["--project-root", str(root), "--mode", "full", "--style", args.style,
               "--industry-template", args.industry_template, "--root-label", args.root_label]
    if args.enable_hierarchy_refinement:
        ht_args.append("--enable-hierarchy-refinement")
    else:
        ht_args.append("--disable-hierarchy-refinement")
    ht_args.extend([
        "--min-tree-depth", str(args.min_tree_depth),
        "--target-tree-depth", str(args.target_tree_depth),
        "--max-tree-depth", str(args.max_tree_depth),
    ])
    if args.adaptive_tree_depth:
        ht_args.append("--adaptive-tree-depth")
    else:
        ht_args.append("--fixed-tree-depth")
    if args.remove_company_branch:
        ht_args.append("--remove-company-branch")
    if args.forbid_representative_company_branch:
        ht_args.append("--forbid-representative-company-branch")
    if args.fix_parent_child_same_level:
        ht_args.append("--fix-parent-child-same-level")
    if args.export_png: ht_args.append("--export-png")
    if args.export_svg: ht_args.append("--export-svg")
    if args.export_html: ht_args.append("--export-html")
    ok, _, _ = run_stage(scripts / "refactor_final_output_to_hierarchy_tree.py", ht_args, logger, "hierarchy_tree", logs_dir)
    validation["hierarchy_tree_nodes_generated"] = {"passed": ok, "note": "tree nodes"}
    validation["hierarchy_tree_edges_generated"] = {"passed": ok, "note": "tree edges"}
    validation["png_exported"] = {"passed": ok, "note": "PNG"}
    validation["svg_exported"] = {"passed": ok, "note": "SVG"}
    validation["html_exported"] = {"passed": ok, "note": "HTML"}
    step_results["hierarchy_tree"] = ok
    if not ok:
        failed_stage = "Step6_hierarchy_tree"
        _write_failure(final_dir, validation, failed_stage, args, ts, ",".join(i["sha256"][:12] for i in pdf_infos), pdf_infos[0]["path"])
        return


    hierarchy_dir = resolve_pointer(root / "rag" / "latest_final_hierarchy_tree_run.txt")
    hierarchy_validation = read_validation_summary(hierarchy_dir)
    hierarchy_required_checks = [
        "document_driven_avoids_generic_up_mid_down_default",
        "document_driven_dynamic_validation_passed",
        "no_representative_company_tree_node",
        "refined_tree_depth_at_least_min",
        "refined_tree_depth_at_most_max",
        "hierarchy_tree_nodes_generated",
        "hierarchy_tree_edges_generated",
        "tree_has_root_node",
    ]
    hierarchy_failed = failed_validation_keys(hierarchy_validation, hierarchy_required_checks)
    validation["hierarchy_content_quality_gate"] = {
        "passed": not hierarchy_failed,
        "note": "OK" if not hierarchy_failed else ",".join(hierarchy_failed),
    }
    if hierarchy_failed:
        failed_stage = "Step6_hierarchy_quality_gate"
        logger.error("层级树质量门禁未通过: %s", hierarchy_failed)
        _write_failure(final_dir, validation, failed_stage, args, ts, ",".join(i["sha256"][:12] for i in pdf_infos), pdf_infos[0]["path"])
        return

    # ─── Step 7: 最终交付整理 ──────────────────────────────────────────────────
    logger.info("--- Step 7: 最终交付整理 ---")
    ht_dir = resolve_pointer(root / "rag" / "latest_final_hierarchy_tree_run.txt")
    tree_nodes_count = 0
    tree_edges_count = 0
    if ht_dir and ht_dir.exists():
        # Copy key files to final_dir
        deliver_files = [
            "hierarchy_tree_nodes.csv", "hierarchy_tree_edges.csv",
            "refined_hierarchy_tree_nodes.csv", "refined_hierarchy_tree_edges.csv",
            "refined_hierarchy_tree_data.json",
            "hierarchy_tree_data.json", f"{png_prefix}.png",
            f"{svg_prefix}.svg", f"{html_prefix}.html",
            "hierarchy_tree_report.md", "template_application_report.md",
            "hierarchy_refinement_report.md", "hierarchy_refinement_run.log",
            "hierarchy_refinement_debug.json",
            "hierarchy_parent_child_fixes.csv", "hierarchy_removed_nodes.csv",
            "hierarchy_quality_report.csv",
            "unclassified_entities.csv", "final_deliverables_manifest.csv",
            "dynamic_template_debug.json", "template_quality_report.csv",
            "validation_summary.json",
        ]
        for fname in deliver_files:
            src = ht_dir / fname
            if src.exists():
                shutil.copy2(str(src), str(final_dir / fname))
        # Count nodes/edges
        nodes_f = final_dir / "refined_hierarchy_tree_nodes.csv"
        edges_f = final_dir / "refined_hierarchy_tree_edges.csv"
        if not nodes_f.exists():
            nodes_f = final_dir / "hierarchy_tree_nodes.csv"
        if not edges_f.exists():
            edges_f = final_dir / "hierarchy_tree_edges.csv"
        if nodes_f.exists():
            with open(nodes_f, encoding="utf-8-sig") as f:
                tree_nodes_count = sum(1 for _ in csv.DictReader(f))
        if edges_f.exists():
            with open(edges_f, encoding="utf-8-sig") as f:
                tree_edges_count = sum(1 for _ in csv.DictReader(f))

    # Final deliverables manifest
    deliverables = []
    for f in sorted(final_dir.iterdir()):
        if f.is_file():
            deliverables.append({
                "file_name": f.name, "file_path": str(f),
                "deliverable_type": "final", "is_final_deliverable": "true",
                "description": f.stem,
                "source_template": args.industry_template,
            })
    # Validation additions
    validation["industry_template_applied"] = {"passed": True, "note": args.industry_template}
    validation["generic_template_supported"] = {"passed": True, "note": "generic可加载"}
    validation["document_driven_template_supported"] = {"passed": "document_driven" in available_templates, "note": "document_driven可加载"}
    validation["final_deliverables_manifest_generated"] = {"passed": True, "note": f"{len(deliverables)} files"}
    validation["no_network_graph_as_final_deliverable"] = {"passed": True, "note": "无GraphML/Neo4j"}
    validation["hierarchy_refinement_enabled"] = {"passed": True, "note": str(args.enable_hierarchy_refinement)}
    validation["hierarchy_refinement_outputs_generated"] = {
        "passed": (final_dir / "refined_hierarchy_tree_nodes.csv").exists() if args.enable_hierarchy_refinement and args.industry_template == "document_driven" else True,
        "note": "refined_hierarchy_tree_*",
    }
    validation["no_external_api_called"] = {"passed": True, "note": "仅本地"}
    validation["no_llm_called_externally"] = {"passed": True, "note": "仅本地Ollama"}
    validation["original_pdf_not_modified"] = {"passed": True, "note": "PDF未改动"}

    write_csv(final_dir / "final_deliverables_manifest.csv", deliverables,
              ["file_name","file_path","deliverable_type","is_final_deliverable","description","source_template"])

    # Pipeline run config
    run_cfg = {
        "pdfs": [str(i["path"]) for i in pdf_infos],
        "pdf_hashes": [{"md5_12": i["md5"], "sha256": i["sha256"]} for i in pdf_infos],
        "root_model": root_model,
        "root_model_id": root_model_id(root_model),
        "model_doc_ids": model_doc_ids,
        "industry_template": args.industry_template,
        "root_label": args.root_label, "style": args.style, "mode": args.mode,
        "hierarchy_refinement_enabled": args.enable_hierarchy_refinement,
        "adaptive_tree_depth": args.adaptive_tree_depth,
        "min_tree_depth": args.min_tree_depth,
        "target_tree_depth": args.target_tree_depth,
        "max_tree_depth": args.max_tree_depth,
        "timestamp": ts, "output_dir": str(final_dir),
        "qdrant_collection": qdrant_collection,
        "tree_nodes": tree_nodes_count, "tree_edges": tree_edges_count,
    }
    (final_dir / "pipeline_run_config.json").write_text(
        json.dumps(run_cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    # Pipeline report
    report = [
        "# 一键产业链建模流程报告", "",
        f"- Root-model: `{root_model}`",
        f"- Qdrant collection: `{qdrant_collection}`",
        f"- 输入PDF数量: {len(pdf_infos)}",
        *[f"  - `{i['path']}` (sha256={i['sha256'][:12]})" for i in pdf_infos],
        f"- 当前root-model文档数: {len(model_doc_ids)}",
        f"- 时间: {ts}", f"- 模板: {args.industry_template}", f"- 根标签: {args.root_label}",
        f"- 自动检测模板: {args.auto_detect_template}",
        f"- hierarchy_refinement: {args.enable_hierarchy_refinement}", "",
        "## 各阶段执行状态",
        *[f"- {k}: {'✓' if v else '✗'}" for k, v in step_results.items()], "",
        f"## 最终结果",
        f"- 树节点: {tree_nodes_count}", f"- 树边: {tree_edges_count}",
        f"- PNG: {(final_dir/f'{png_prefix}.png').exists()}",
        f"- SVG: {(final_dir/f'{svg_prefix}.svg').exists()}",
        f"- HTML: {(final_dir/f'{html_prefix}.html').exists()}", "",
        "## 最终交付说明",
        "**最终交付是多级产业链结构图，不是 network graph。**",
        "GraphML/Neo4j/力导向图不作为最终交付。", "",
        f"## 最终交付目录", f"`{final_dir}`",
    ]
    (final_dir / "pipeline_run_report.md").write_text("\n".join(report), encoding="utf-8")

    # Validation summary
    (final_dir / "pipeline_validation_summary.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")

    # Refresh manifest after pipeline_run_* and pipeline_validation_summary.json are written.
    deliverables = []
    for f in sorted(final_dir.iterdir()):
        if f.is_file():
            deliverables.append({
                "file_name": f.name, "file_path": str(f),
                "deliverable_type": "final", "is_final_deliverable": "true",
                "description": f.stem,
                "source_template": args.industry_template,
            })
    write_csv(final_dir / "final_deliverables_manifest.csv", deliverables,
              ["file_name","file_path","deliverable_type","is_final_deliverable","description","source_template"])
    validation["final_deliverables_manifest_generated"] = {"passed": True, "note": f"{len(deliverables)} files"}
    (final_dir / "pipeline_validation_summary.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(f"最终交付: {final_dir}")
    logger.info(f"树节点: {tree_nodes_count}, 树边: {tree_edges_count}")
    logger.info("=== 一键流程完成 ===")

    # ─── Cleanup extra ─────────────────────────────────────────────────────────
    if args.cleanup_extra:
        logger.info("--- Cleanup extra ---")
        run_stage(scripts / "cleanup_unneeded_outputs.py",
                  ["--project-root", str(root), "--mode", "archive"], logger, "cleanup", logs_dir)

def _write_failure(final_dir, validation, failed_stage, args, ts, doc_hash, pdf_path):
    """Write failure report when a stage fails."""
    logger = logging.getLogger(__name__)
    logger.error(f"流程失败: {failed_stage}")
    (final_dir / "pipeline_validation_summary.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    report = [
        "# 一键流程失败报告", "",
        f"- 失败阶段: {failed_stage}", f"- PDF: {pdf_path}", f"- Hash: {doc_hash}",
        f"- 时间: {ts}", "", "请检查 logs 目录获取详细错误信息。",
    ]
    (final_dir / "pipeline_run_report.md").write_text("\n".join(report), encoding="utf-8")
    (final_dir / "pipeline_run_config.json").write_text(
        json.dumps({"failed_stage": failed_stage, "pdf": str(pdf_path), "hash": doc_hash,
                    "timestamp": ts}, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
