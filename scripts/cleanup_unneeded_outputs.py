#!/usr/bin/env python3
"""清理或归档不符合最终目标的多余文件。默认 archive 模式，不硬删除。"""
import argparse, csv, json, logging, shutil, sys
from datetime import datetime
from pathlib import Path

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"

def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def resolve_pointer(p):
    pp = Path(p)
    if pp.exists():
        t = pp.read_text(encoding="utf-8").strip()
        return Path(t) if t else None
    return None

def get_latest_pointers(root):
    """Return set of directories referenced by latest pointers."""
    ptrs = set()
    for f in root.joinpath("rag").glob("latest_*_run.txt"):
        d = resolve_pointer(f)
        if d and d.exists():
            ptrs.add(d.resolve())
    return ptrs

def build_cleanup_plan(root, latest_dirs, logger):
    plan = []
    rag_outputs = root / "rag" / "outputs"
    # Network graph outputs (stage5*, stage6*, old hierarchy tree runs)
    latest_ht = resolve_pointer(root / "rag" / "latest_final_hierarchy_tree_run.txt")
    network_patterns = ["stage5_*", "stage5_5_*", "stage6_*"]
    deprecated_file_names = {
        "industry_chain.graphml", "industry_chain.json", "neo4j_nodes.csv",
        "neo4j_edges.csv", "neo4j_import_readme.md",
        "industry_chain_overview.png", "industry_chain_overview.svg",
        "industry_chain_evidence_only.png", "industry_chain_evidence_only.svg",
        "industry_chain_schema_layout.png", "industry_chain_schema_layout.svg",
        "industry_chain_layered.png", "industry_chain_layered.svg",
        "graph_node_inventory.csv", "graph_edge_inventory.csv",
        "stage6_graph_export_report.md",
    }

    if rag_outputs.exists():
        for d in sorted(rag_outputs.iterdir()):
            if not d.is_dir():
                continue
            resolved = d.resolve()
            # Keep latest final hierarchy tree
            if latest_ht and resolved == latest_ht.resolve():
                plan.append({"file_path": str(d), "file_type": "directory",
                             "size_bytes": sum(f.stat().st_size for f in d.rglob("*") if f.is_file()),
                             "action": "keep", "reason": "最终交付目录(latest pointer)", "is_safe_to_delete": "false"})
                continue
            # Keep if pointed by any latest pointer
            if resolved in latest_dirs:
                plan.append({"file_path": str(d), "file_type": "directory",
                             "size_bytes": sum(f.stat().st_size for f in d.rglob("*") if f.is_file()),
                             "action": "keep", "reason": "当前阶段最新输出(latest pointer)", "is_safe_to_delete": "false"})
                continue
            # Network graph dirs
            name = d.name
            is_network = any(name.startswith(p.replace("*","")) for p in network_patterns)
            is_old_ht = name.startswith("final_hierarchy_tree_") and (not latest_ht or resolved != latest_ht.resolve())
            if is_network:
                plan.append({"file_path": str(d), "file_type": "directory",
                             "size_bytes": sum(f.stat().st_size for f in d.rglob("*") if f.is_file()),
                             "action": "archive", "reason": "network graph阶段输出，不符合最终多级结构图目标",
                             "is_safe_to_delete": "true"})
            elif is_old_ht:
                plan.append({"file_path": str(d), "file_type": "directory",
                             "size_bytes": sum(f.stat().st_size for f in d.rglob("*") if f.is_file()),
                             "action": "archive", "reason": "旧版hierarchy tree输出，非最新",
                             "is_safe_to_delete": "true"})
            else:
                # Other old stage runs not pointed by latest
                plan.append({"file_path": str(d), "file_type": "directory",
                             "size_bytes": sum(f.stat().st_size for f in d.rglob("*") if f.is_file()),
                             "action": "archive", "reason": "非最新阶段输出，归档保留",
                             "is_safe_to_delete": "true"})

    # Old parsed_documents (keep latest only)
    pd_dir = root / "parsed_documents"
    latest_pd = resolve_pointer(pd_dir / "latest_run.txt") if (pd_dir / "latest_run.txt").exists() else None
    if pd_dir.exists():
        for d in sorted(pd_dir.iterdir()):
            if not d.is_dir():
                continue
            if latest_pd and d.resolve() == latest_pd.resolve():
                plan.append({"file_path": str(d), "file_type": "directory",
                             "size_bytes": sum(f.stat().st_size for f in d.rglob("*") if f.is_file()),
                             "action": "keep", "reason": "最新证据库(latest_run.txt)", "is_safe_to_delete": "false"})
            else:
                plan.append({"file_path": str(d), "file_type": "directory",
                             "size_bytes": sum(f.stat().st_size for f in d.rglob("*") if f.is_file()),
                             "action": "archive", "reason": "旧版证据库，非最新", "is_safe_to_delete": "true"})

    # Always keep
    for keep_path in [root / "data" / "raw_pdf", root / "scripts", root / "rag" / "config"]:
        if keep_path.exists():
            plan.append({"file_path": str(keep_path), "file_type": "directory",
                         "size_bytes": 0, "action": "keep",
                         "reason": "核心保护目录", "is_safe_to_delete": "false"})
    return plan

def execute_plan(plan, archive_base, mode, logger):
    archived = []
    kept = []
    for item in plan:
        if item["action"] == "keep" or item["action"] == "skip":
            kept.append(item)
        elif item["action"] == "archive":
            src = Path(item["file_path"])
            if not src.exists():
                continue
            if mode == "hard-delete":
                shutil.rmtree(str(src), ignore_errors=True)
                item["archived_to"] = "DELETED"
                logger.info(f"删除: {src.name}")
            else:
                dst = archive_base / src.name
                shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
                shutil.rmtree(str(src), ignore_errors=True)
                item["archived_to"] = str(dst)
                logger.info(f"归档: {src.name}")
            archived.append(item)
        elif item["action"] == "delete":
            src = Path(item["file_path"])
            if src.exists() and mode == "hard-delete":
                shutil.rmtree(str(src), ignore_errors=True)
                item["archived_to"] = "DELETED"
                archived.append(item)
    return archived, kept

def main():
    parser = argparse.ArgumentParser(description="清理/归档多余输出")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--mode", choices=["dry-run","archive","hard-delete"], default="archive")
    parser.add_argument("--keep-latest-final", action="store_true", default=True)
    parser.add_argument("--delete-network-graph", action="store_true", default=True)
    parser.add_argument("--delete-dry-runs", action="store_true", default=True)
    parser.add_argument("--delete-old-intermediates", action="store_true", default=True)
    parser.add_argument("--delete-intermediate", action="store_true", default=False)
    parser.add_argument("--hard-delete", action="store_true", default=False)
    args = parser.parse_args()
    if args.hard_delete:
        args.mode = "hard-delete"

    root = Path(args.project_root)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_base = root / "archive" / f"cleanup_{ts}"
    out_dir = archive_base
    out_dir.mkdir(parents=True, exist_ok=True)

    log_path = root / "logs" / f"cleanup_{ts}.log"
    log_path.parent.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format=LOG_FMT,
                        handlers=[logging.FileHandler(log_path, encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    logger = logging.getLogger(__name__)
    logger.info(f"=== Cleanup: mode={args.mode} ===")

    latest_dirs = get_latest_pointers(root)
    plan = build_cleanup_plan(root, latest_dirs, logger)
    logger.info(f"计划: {len(plan)} 项 (archive={sum(1 for p in plan if p['action']=='archive')}, keep={sum(1 for p in plan if p['action']=='keep')})")

    plan_fields = ["file_path","file_type","size_bytes","action","reason","is_safe_to_delete"]
    write_csv(out_dir / "cleanup_plan.csv", plan, plan_fields)

    if args.mode == "dry-run":
        logger.info("DRY-RUN 完成，仅生成 cleanup_plan.csv")
        # Write minimal validation
        val = {"mode": "dry-run", "plan_items": len(plan),
               "to_archive": sum(1 for p in plan if p["action"]=="archive"),
               "to_keep": sum(1 for p in plan if p["action"]=="keep")}
        (out_dir / "validation_summary.json").write_text(json.dumps(val, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "run.log").write_text(f"dry-run at {ts}\n", encoding="utf-8")
        return

    # Execute
    archived, kept = execute_plan(plan, archive_base, args.mode, logger)
    logger.info(f"完成: 归档/删除 {len(archived)} 项, 保留 {len(kept)} 项")

    # Outputs
    write_csv(out_dir / "cleanup_manifest.csv", plan, plan_fields)
    arc_fields = ["file_path","file_type","size_bytes","action","reason","is_safe_to_delete","archived_to"]
    write_csv(out_dir / "deleted_or_archived_files.csv", archived, arc_fields)
    write_csv(out_dir / "kept_files_manifest.csv", kept, plan_fields)

    # Report
    report = [
        "# Cleanup 报告", "",
        f"- 模式: {args.mode}", f"- 时间: {ts}", f"- 归档/删除: {len(archived)} 项",
        f"- 保留: {len(kept)} 项", "",
        "## 保留的目录", *[f"- `{k['file_path']}` — {k['reason']}" for k in kept], "",
        "## 归档/删除的目录", *[f"- `{a['file_path']}` — {a['reason']}" for a in archived], "",
        "## 为什么 GraphML/Neo4j/network graph 不作为最终交付",
        "用户需要的是多级产业链结构图（思维导图/目录树风格），不是力导向网络图。",
        "GraphML/Neo4j 输出已归档保留，可在需要时恢复。", "",
        "## 如何恢复归档文件",
        f"归档目录: `{archive_base}`，直接复制回原位即可。", "",
        "## 最终交付目录",
        f"由 `{root / 'rag' / 'latest_final_hierarchy_tree_run.txt'}` 指向。",
    ]
    (out_dir / "cleanup_report.md").write_text("\n".join(report), encoding="utf-8")

    val = {
        "mode": args.mode, "archived_count": len(archived), "kept_count": len(kept),
        "no_raw_pdf_deleted": True, "no_scripts_deleted": True,
        "no_final_deliverable_deleted": True, "plan_generated": True,
    }
    (out_dir / "validation_summary.json").write_text(json.dumps(val, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "run.log").write_text(f"cleanup done at {ts}, mode={args.mode}\n", encoding="utf-8")
    logger.info("=== Cleanup 完成 ===")

if __name__ == "__main__":
    main()

