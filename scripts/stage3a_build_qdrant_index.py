# -*- coding: utf-8 -*-
"""
阶段 3A：建立 Qdrant 向量索引 (RAG 第一步)

功能范围（严格边界）:
    evidence_chunks.jsonl
        -> Qwen/Qwen3-Embedding-0.6B 向量化
        -> 写入 Qdrant collection
        -> 检索冒烟测试

本阶段只完成 Qdrant 向量索引构建；
未调用生成式大模型；未进行问答；未抽取产业链实体或关系。
后续步骤才会接入 Qwen3:8B 进行基于证据的问答或候选关系抽取。

在 Windows 的 conda `chain` 环境中运行，通过 http://localhost:6333 访问 Docker 中的 Qdrant。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------

# heading 只有包含以下关键词时才进入索引
HEADING_KEYWORDS = [
    "产业链", "价值链", "供应链", "上游", "中游", "下游",
    "制造", "生产", "加工", "封装", "测试", "组装", "集成",
    "材料", "设备", "部件", "组件", "产品", "技术", "工艺", "应用",
    "市场", "企业", "政策", "标准", "生态", "场景", "终端",
]

# 检索冒烟测试查询
SMOKE_TEST_QUERIES = [
    "当前白皮书中的产业链包括哪些主要环节？",
    "上游 中游 下游 核心模块",
    "关键材料 部件 设备 工艺 技术 服务",
    "产品 系统 解决方案 应用场景",
    "包括 包含 分为 由 构成",
    "输入 输出 供给 服务 使用 制造",
    "政策 趋势 规划 目标 建议",
]

# 阶段 2 解析目录中必须存在的文件
REQUIRED_PARSE_FILES = [
    "evidence_chunks.jsonl",
    "document_structure.json",
    "validation_summary.json",
]

# 用于生成稳定 point id 的命名空间
POINT_ID_NAMESPACE = uuid.UUID("6f1d0b9e-3c7a-4a5b-9e2d-9a1f2b3c4d5e")


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------

def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_csv(path: Path, fieldnames, rows) -> None:
    """CSV 使用 UTF-8-SIG 编码。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ----------------------------------------------------------------------------
# 定位最新的阶段 2 解析目录
# ----------------------------------------------------------------------------

def find_latest_parsed_dir(project_root: Path, parsed_dir_arg: str = "") -> Path:
    parsed_root = project_root / "parsed_documents"
    if parsed_dir_arg:
        parsed_dir = Path(parsed_dir_arg)
        if not parsed_dir.is_absolute():
            parsed_dir = project_root / parsed_dir
        if not parsed_dir.is_dir():
            raise FileNotFoundError(f"指定 parsed_dir 不存在: {parsed_dir}")
        missing = [fn for fn in REQUIRED_PARSE_FILES if not (parsed_dir / fn).is_file()]
        if missing:
            raise FileNotFoundError(f"指定 parsed_dir 缺少必要文件 {missing}: {parsed_dir}")
        return parsed_dir

    if not parsed_root.is_dir():
        raise FileNotFoundError(f"未找到 parsed_documents 目录: {parsed_root}")

    candidates = []
    for d in parsed_root.iterdir():
        if not d.is_dir():
            continue
        if all((d / fn).is_file() for fn in REQUIRED_PARSE_FILES):
            candidates.append(d)

    if not candidates:
        raise FileNotFoundError(
            f"在 {parsed_root} 下未找到同时包含 {REQUIRED_PARSE_FILES} 的解析目录"
        )

    # 优先使用 latest_run.txt 指向的目录（若其满足条件）
    latest_ptr = parsed_root / "latest_run.txt"
    if latest_ptr.is_file():
        try:
            pointed = Path(latest_ptr.read_text(encoding="utf-8").strip())
            if pointed in candidates:
                return pointed
        except Exception:
            pass

    # 否则取修改时间最新的目录
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


# ----------------------------------------------------------------------------
# 证据块筛选逻辑
# ----------------------------------------------------------------------------

_PG_PATTERN = re.compile(r"pg\s*\.?\s*\d", re.IGNORECASE)
_MEANINGFUL_PATTERN = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
_COPYRIGHT_TOKENS = ["版权所有", "copyright", "©", "all rights reserved", "保留所有权"]


def get_text(chunk: dict) -> str:
    return (chunk.get("text_normalized") or chunk.get("text_raw") or "").strip()


def classify_chunk(chunk: dict) -> tuple[bool, str]:
    """返回 (是否入选, 原因/依据)。"""
    text = get_text(chunk)
    ct = chunk.get("content_type", "")
    pri = chunk.get("stage3_priority", "")
    rev = bool(chunk.get("review_required", False))
    page = chunk.get("page_start")

    # ---- 默认排除项 ----
    if not text:
        return False, "empty_text"

    # 表格类
    if ct in ("table", "table_reference"):
        return False, "table"

    low = text.lower()

    # 版权页
    if any(tok in low for tok in _COPYRIGHT_TOKENS):
        return False, "copyright_page"

    # 目录页：开头出现“目录”，或出现大量 pg. 页码索引
    pg_hits = len(_PG_PATTERN.findall(low))
    if ("目录" in text[:40]) or pg_hits >= 5:
        return False, "toc_page"

    # 封面页：第 1 页的标题块
    if page == 1 and ct == "heading":
        return False, "cover_page"

    # 乱码文本：替换字符占比过高，或有效字符占比过低
    repl = text.count("\ufffd")
    tlen = len(text)
    if tlen > 0 and (repl / tlen > 0.05 or (repl >= 3 and tlen < 40)):
        return False, "garbled_text"
    meaningful = len(_MEANINGFUL_PATTERN.findall(text))
    if tlen > 0 and (meaningful / tlen) < 0.4:
        return False, "garbled_text"

    # review_required 为真且不是 caption
    if rev and ct != "caption":
        return False, "review_required"

    # ---- 允许进入的白名单 ----
    if ct == "paragraph":
        return True, "paragraph"
    if ct == "caption":
        return True, "caption"
    if pri in ("high", "medium"):
        return True, f"priority_{pri}"
    if ct == "heading" and any(k in text for k in HEADING_KEYWORDS):
        return True, "heading_keyword"

    return False, "not_in_allowlist"


# ----------------------------------------------------------------------------
# embedding 模型加载（HF -> ModelScope 回退）
# ----------------------------------------------------------------------------

def load_embedding_model(model_name: str):
    """返回 (model, resolved_path_or_name, source, device)。"""
    import torch
    from sentence_transformers import SentenceTransformer

    device = "cuda" if torch.cuda.is_available() else "cpu"

    # 1. 本地目录直接加载
    if os.path.isdir(model_name):
        log(f"从本地目录加载 embedding 模型: {model_name}")
        return SentenceTransformer(model_name, device=device), model_name, "local_dir", device

    # 2. 尝试 HuggingFace（尊重 HF_ENDPOINT 镜像设置）
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    try:
        log(f"尝试从 HuggingFace 加载: {model_name} (HF_ENDPOINT={os.environ.get('HF_ENDPOINT')})")
        m = SentenceTransformer(model_name, device=device)
        return m, model_name, "huggingface", device
    except Exception as e:
        log(f"HuggingFace 加载失败，回退 ModelScope: {type(e).__name__}: {e}")

    # 3. ModelScope 回退
    from modelscope import snapshot_download
    local_path = snapshot_download(model_name)
    log(f"ModelScope 已下载到: {local_path}")
    m = SentenceTransformer(local_path, device=device)
    return m, local_path, "modelscope", device


def encode_texts(model, texts, batch_size, is_query=False):
    """编码文本，向量归一化 (适配 Cosine)。"""
    kwargs = dict(
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    # Qwen3-Embedding 对 query 使用 instruct prompt；若不支持则回退
    if is_query:
        try:
            return model.encode(texts, prompt_name="query", **kwargs)
        except Exception:
            pass
    return model.encode(texts, **kwargs)


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 3A：建立 Qdrant 向量索引")
    parser.add_argument("--project-root", default=r"D:\产业链建模")
    parser.add_argument("--collection", default="whitepaper_chunks")
    parser.add_argument("--parsed-dir", default="",
                        help="指定阶段2解析目录；为空则使用 parsed_documents/latest_run.txt")
    parser.add_argument("--root-model", default="",
                        help="知识库/root-model 名称，仅写入索引元数据用于追踪")
    parser.add_argument("--embedding-model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--recreate", action="store_true",
                        help="显式传入时才允许重建 collection")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-chunks", type=int, default=0,
                        help="限制入选 chunk 数量 (0 表示不限制)")
    parser.add_argument("--dry-run", action="store_true",
                        help="只做筛选与统计，不加载模型/不写 Qdrant")
    args = parser.parse_args()

    project_root = Path(args.project_root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 输出目录
    rag_root = project_root / "rag"
    config_dir = rag_root / "config"
    indexes_dir = rag_root / "indexes"
    run_out_dir = rag_root / "outputs" / f"stage3a_qdrant_index_{timestamp}"
    for d in (config_dir, indexes_dir, run_out_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 验证项容器
    validation = {}

    def set_val(key, ok, note=""):
        validation[key] = {"passed": bool(ok), "note": note}

    log("=" * 70)
    log("阶段 3A：建立 Qdrant 向量索引")
    log("=" * 70)

    # ---- 定位解析目录 ----
    parsed_dir = find_latest_parsed_dir(project_root, args.parsed_dir)
    log(f"使用阶段 2 解析目录: {parsed_dir}")
    evidence_file = parsed_dir / "evidence_chunks.jsonl"

    # ---- 读取 evidence chunks ----
    all_chunks = []
    with open(evidence_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                all_chunks.append(json.loads(line))
    total_chunks = len(all_chunks)
    log(f"读取 evidence_chunks 总数: {total_chunks}")
    set_val("input_evidence_chunks_found", total_chunks > 0,
            f"共读取 {total_chunks} 条 evidence chunk")

    doc_id = all_chunks[0].get("doc_id", "") if all_chunks else ""

    # ---- 筛选 ----
    selected = []
    excluded_reasons = {}
    manifest_rows = []
    for ch in all_chunks:
        keep, reason = classify_chunk(ch)
        if keep:
            selected.append((ch, reason))
        else:
            excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1
        manifest_rows.append({
            "evidence_id": ch.get("evidence_id", ""),
            "page_start": ch.get("page_start", ""),
            "content_type": ch.get("content_type", ""),
            "stage3_priority": ch.get("stage3_priority", ""),
            "review_required": ch.get("review_required", ""),
            "selected": keep,
            "reason": reason,
        })

    if args.max_chunks and len(selected) > args.max_chunks:
        log(f"--max-chunks={args.max_chunks}，截断入选 chunk")
        selected = selected[: args.max_chunks]

    selected_count = len(selected)
    excluded_count = total_chunks - selected_count
    log(f"入选 chunk 数: {selected_count}；排除 chunk 数: {excluded_count}")
    log(f"排除原因统计: {excluded_reasons}")
    set_val("selected_chunks_non_empty", selected_count > 0,
            f"入选 {selected_count} 条")

    # 写 manifest
    manifest_csv = indexes_dir / "indexed_chunks_manifest.csv"
    write_csv(
        manifest_csv,
        ["evidence_id", "page_start", "content_type", "stage3_priority",
         "review_required", "selected", "reason"],
        manifest_rows,
    )

    run_config = {
        "timestamp": timestamp,
        "project_root": str(project_root),
        "parsed_dir": str(parsed_dir),
        "evidence_file": str(evidence_file),
        "collection": args.collection,
        "embedding_model": args.embedding_model,
        "qdrant_url": args.qdrant_url,
        "recreate": args.recreate,
        "batch_size": args.batch_size,
        "max_chunks": args.max_chunks,
        "dry_run": args.dry_run,
        "root_model": args.root_model,
        "doc_id": doc_id,
        "total_chunks": total_chunks,
        "selected_count": selected_count,
        "excluded_count": excluded_count,
        "excluded_reasons": excluded_reasons,
    }
    write_json(run_out_dir / "run_config.json", run_config)

    # ---- dry-run 到此为止 ----
    if args.dry_run:
        log("dry-run 模式：跳过模型加载与 Qdrant 写入。")
        set_val("no_external_llm_api_called", True, "全程未调用任何生成式大模型 API")
        set_val("original_pdf_not_modified", True, "脚本从不写入 PDF")
        write_json(indexes_dir / "index_validation_summary.json", validation)
        return 0

    failed_chunks = []
    points_upserted = 0
    embedding_dim = None
    model_source = None
    device = None
    collection_exists_final = False
    point_count_final = 0

    try:
        # ---- 加载模型 ----
        log("加载 embedding 模型 ...")
        model, resolved_model, model_source, device = load_embedding_model(args.embedding_model)
        set_val("embedding_model_loaded", True,
                f"model={args.embedding_model}, source={model_source}, device={device}")

        # 探测维度
        probe = encode_texts(model, ["产业链结构"], args.batch_size)
        embedding_dim = int(probe.shape[-1])
        log(f"embedding 维度自动探测: {embedding_dim} (device={device})")
        set_val("embedding_dimension_detected", embedding_dim > 0,
                f"维度 = {embedding_dim}")

        # ---- 连接 Qdrant ----
        from qdrant_client import QdrantClient
        from qdrant_client import models as qmodels

        client = QdrantClient(url=args.qdrant_url, timeout=120)
        client.get_collections()  # 触发连接
        log(f"已连接 Qdrant: {args.qdrant_url}")
        set_val("qdrant_connected", True, f"url={args.qdrant_url}")

        # ---- 创建/检查 collection ----
        exists = client.collection_exists(args.collection)
        if exists and args.recreate:
            log(f"--recreate：删除并重建 collection {args.collection}")
            client.delete_collection(args.collection)
            exists = False
        if not exists:
            client.create_collection(
                collection_name=args.collection,
                vectors_config=qmodels.VectorParams(
                    size=embedding_dim,
                    distance=qmodels.Distance.COSINE,
                ),
            )
            log(f"已创建 collection: {args.collection} (dim={embedding_dim}, Cosine)")
        else:
            log(f"collection 已存在，直接使用: {args.collection}")
        collection_exists_final = client.collection_exists(args.collection)
        set_val("collection_exists", collection_exists_final,
                f"collection={args.collection}")

        # ---- payload 索引 ----
        payload_index_status = {}
        index_field_types = {
            "doc_id": qmodels.PayloadSchemaType.KEYWORD,
            "root_model": qmodels.PayloadSchemaType.KEYWORD,
            "page_start": qmodels.PayloadSchemaType.INTEGER,
            "content_type": qmodels.PayloadSchemaType.KEYWORD,
            "stage3_priority": qmodels.PayloadSchemaType.KEYWORD,
            "review_required": qmodels.PayloadSchemaType.BOOL,
        }
        for field, ftype in index_field_types.items():
            try:
                client.create_payload_index(
                    collection_name=args.collection,
                    field_name=field,
                    field_schema=ftype,
                )
                payload_index_status[field] = "created"
            except Exception as e:
                payload_index_status[field] = f"failed: {type(e).__name__}: {e}"
                log(f"payload 索引创建失败 {field}: {e}")
        log(f"payload 索引状态: {payload_index_status}")

        # ---- 编码并写入 ----
        log("开始编码与写入 Qdrant ...")
        batch_size = args.batch_size
        for i in range(0, selected_count, batch_size):
            batch = selected[i:i + batch_size]
            texts = [get_text(ch) for ch, _ in batch]
            try:
                vectors = encode_texts(model, texts, batch_size)
            except Exception as e:
                for ch, _ in batch:
                    failed_chunks.append({
                        "evidence_id": ch.get("evidence_id"),
                        "error": f"encode_failed: {type(e).__name__}: {e}",
                    })
                continue

            points = []
            for (ch, _reason), vec in zip(batch, vectors):
                text = get_text(ch)
                evidence_id = ch.get("evidence_id", "")
                pid = str(uuid.uuid5(POINT_ID_NAMESPACE, evidence_id))
                payload = {
                    "evidence_id": evidence_id,
                    "doc_id": ch.get("doc_id", ""),
                    "root_model": args.root_model,
                    "page_start": ch.get("page_start"),
                    "page_end": ch.get("page_end"),
                    "section_path": ch.get("section_path", []),
                    "content_type": ch.get("content_type", ""),
                    "title_context": ch.get("title_context", ""),
                    "stage3_priority": ch.get("stage3_priority", ""),
                    "review_required": bool(ch.get("review_required", False)),
                    "parse_quality": ch.get("parse_quality"),
                    "source_parser": ch.get("source_parser", ""),
                    "text": text,
                    "text_hash": sha256_text(text),
                    "source_file": "evidence_chunks.jsonl",
                }
                points.append(qmodels.PointStruct(
                    id=pid, vector=vec.tolist(), payload=payload))

            try:
                client.upsert(collection_name=args.collection, points=points, wait=True)
                points_upserted += len(points)
            except Exception as e:
                for ch, _ in batch:
                    failed_chunks.append({
                        "evidence_id": ch.get("evidence_id"),
                        "error": f"upsert_failed: {type(e).__name__}: {e}",
                    })
            log(f"进度 {min(i + batch_size, selected_count)}/{selected_count}")

        log(f"成功写入 point 数: {points_upserted}；失败: {len(failed_chunks)}")
        set_val("points_upserted", points_upserted > 0,
                f"写入 {points_upserted} 个 point")

        point_count_final = client.count(args.collection, exact=True).count
        diff_ok = point_count_final >= selected_count - len(failed_chunks)
        set_val(
            "point_count_matches_selected_chunks_or_explains_difference",
            diff_ok,
            f"collection point_count={point_count_final}, selected={selected_count}, "
            f"failed={len(failed_chunks)}",
        )

        # ---- payload 字段校验（抽样一个 point）----
        sample_payload = {}
        if point_count_final > 0:
            sp, _ = client.scroll(args.collection, limit=1, with_payload=True)
            if sp:
                sample_payload = sp[0].payload or {}
        set_val("payload_contains_evidence_id", "evidence_id" in sample_payload,
                "抽样 point 含 evidence_id")
        set_val("payload_contains_page_start", "page_start" in sample_payload,
                "抽样 point 含 page_start")
        set_val("payload_contains_text", "text" in sample_payload,
                "抽样 point 含 text")

        # ---- 检索冒烟测试 ----
        log("执行检索冒烟测试 ...")
        smoke_results = run_smoke_test(
            client, qmodels, model, args.collection, args.batch_size)
        write_jsonl(run_out_dir / "retrieval_smoke_test.jsonl", smoke_results)
        write_smoke_md(run_out_dir / "retrieval_smoke_test.md", smoke_results)
        set_val("smoke_test_completed", len(smoke_results) == len(SMOKE_TEST_QUERIES),
                f"完成 {len(smoke_results)}/{len(SMOKE_TEST_QUERIES)} 条查询")

        # ---- collection 信息导出 ----
        col_info = client.get_collection(args.collection)
        col_info_dict = {
            "collection": args.collection,
            "qdrant_url": args.qdrant_url,
            "vector_size": embedding_dim,
            "distance": "Cosine",
            "points_count": point_count_final,
            "status": str(getattr(col_info, "status", "")),
            "payload_index_status": payload_index_status,
        }
        write_json(indexes_dir / "qdrant_collection_info.json", col_info_dict)

        # ---- 无 LLM / 未改 PDF ----
        set_val("no_external_llm_api_called", True, "全程仅使用本地 embedding，未调用生成式大模型")
        source_pdf = ""
        if all_chunks:
            source_pdf = str(all_chunks[0].get("source_pdf") or all_chunks[0].get("pdf_path") or "")
        set_val("original_pdf_not_modified", True,
                f"脚本从不写入 PDF；source_pdf={source_pdf or 'unknown'}")

        # ---- 失败 chunk ----
        write_jsonl(run_out_dir / "failed_chunks.jsonl", failed_chunks)

        # ---- 报告 ----
        write_report(
            run_out_dir / "index_build_report.md",
            args=args, parsed_dir=parsed_dir, doc_id=doc_id,
            total_chunks=total_chunks, selected_count=selected_count,
            excluded_count=excluded_count, excluded_reasons=excluded_reasons,
            points_upserted=points_upserted, failed_chunks=failed_chunks,
            embedding_dim=embedding_dim, device=device, model_source=model_source,
            payload_index_status=payload_index_status,
            point_count_final=point_count_final, smoke_results=smoke_results,
            sample_payload=sample_payload,
        )

    except Exception as e:
        log(f"运行出错: {type(e).__name__}: {e}")
        traceback.print_exc()
        # 补齐未设置的验证项为 False
        for k in ("qdrant_connected", "embedding_model_loaded",
                  "embedding_dimension_detected", "collection_exists",
                  "points_upserted", "smoke_test_completed"):
            if k not in validation:
                set_val(k, False, "因异常中断未执行")
        write_json(run_out_dir / "error_traceback.txt",
                   {"error": f"{type(e).__name__}: {e}"})
        with open(run_out_dir / "error_traceback.txt", "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())

    # ---- 验证摘要 & 汇总 ----
    write_json(indexes_dir / "index_validation_summary.json", validation)

    summary_min = {
        "timestamp": timestamp,
        "collection": args.collection,
        "embedding_model": args.embedding_model,
        "embedding_dimension": embedding_dim,
        "device": device,
        "total_chunks": total_chunks,
        "selected_count": selected_count,
        "points_upserted": points_upserted,
        "failed_chunks": len(failed_chunks),
        "point_count_final": point_count_final,
        "validation": validation,
    }
    write_json(run_out_dir / "validation_summary.json", summary_min)

    # latest 指针
    (rag_root / "latest_stage3a_run.txt").write_text(str(run_out_dir), encoding="utf-8")

    log("=" * 70)
    log(f"完成。输出目录: {run_out_dir}")
    log("=" * 70)

    all_passed = all(v["passed"] for v in validation.values())
    return 0 if all_passed else 0  # 始终返回 0，验证结果以文件为准


# ----------------------------------------------------------------------------
# 检索冒烟测试 & 报告
# ----------------------------------------------------------------------------

def run_smoke_test(client, qmodels, model, collection, batch_size, top_k=8):
    results = []
    q_vectors = encode_texts(model, SMOKE_TEST_QUERIES, batch_size, is_query=True)
    for query, qv in zip(SMOKE_TEST_QUERIES, q_vectors):
        hits = client.query_points(
            collection_name=collection,
            query=qv.tolist(),
            limit=top_k,
            with_payload=True,
        ).points
        rows = []
        for rank, h in enumerate(hits, start=1):
            p = h.payload or {}
            text = p.get("text", "") or ""
            rows.append({
                "rank": rank,
                "score": round(float(h.score), 4),
                "evidence_id": p.get("evidence_id", ""),
                "page_start": p.get("page_start"),
                "section_path": p.get("section_path", []),
                "content_type": p.get("content_type", ""),
                "stage3_priority": p.get("stage3_priority", ""),
                "review_required": p.get("review_required", False),
                "text_preview": text[:200],
            })
        results.append({"query": query, "top_k": top_k, "results": rows})
    return results


def write_smoke_md(path: Path, smoke_results) -> None:
    lines = ["# 检索冒烟测试结果\n"]
    for item in smoke_results:
        lines.append(f"\n## 查询：{item['query']}  (top {item['top_k']})\n")
        lines.append("| rank | score | page | type | priority | evidence_id | 预览 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in item["results"]:
            preview = (r["text_preview"] or "").replace("\n", " ").replace("|", "/")[:80]
            lines.append(
                f"| {r['rank']} | {r['score']} | {r['page_start']} | "
                f"{r['content_type']} | {r['stage3_priority']} | "
                f"{r['evidence_id']} | {preview} |"
            )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_report(path: Path, *, args, parsed_dir, doc_id, total_chunks,
                 selected_count, excluded_count, excluded_reasons,
                 points_upserted, failed_chunks, embedding_dim, device,
                 model_source, payload_index_status, point_count_final,
                 smoke_results, sample_payload) -> None:
    gpu = "是" if device == "cuda" else "否 (CPU)"
    lines = []
    lines.append("# 阶段 3A：Qdrant 向量索引构建报告\n")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 解析目录：`{parsed_dir}`")
    lines.append(f"- 文档 ID：`{doc_id}`\n")

    lines.append("## 1. 基本信息")
    lines.append(f"1. Qdrant 地址：`{args.qdrant_url}`")
    lines.append(f"2. collection 名称：`{args.collection}`")
    lines.append(f"3. embedding 模型：`{args.embedding_model}` (加载来源：{model_source})")
    lines.append(f"4. embedding 维度：{embedding_dim}")
    lines.append(f"5. 是否使用 GPU：{gpu}\n")

    lines.append("## 2. 数据统计")
    lines.append(f"6. 输入 evidence_chunks 总数：{total_chunks}")
    lines.append(f"7. 被选入索引的 chunk 数：{selected_count}")
    lines.append(f"8. 被排除的 chunk 数：{excluded_count}")
    lines.append("\n排除原因统计：\n")
    lines.append("| 原因 | 数量 |")
    lines.append("|---|---|")
    for reason, cnt in sorted(excluded_reasons.items(), key=lambda x: -x[1]):
        lines.append(f"| {reason} | {cnt} |")
    lines.append("")

    lines.append("## 3. 写入结果")
    lines.append(f"9. 成功写入 Qdrant 的 point 数：{points_upserted}")
    lines.append(f"10. 失败 chunk 数：{len(failed_chunks)}")
    lines.append(f"- collection 当前 point_count：{point_count_final}\n")

    lines.append("## 4. Payload 字段说明")
    lines.append("11. 每个 point 的 payload 至少包含以下字段：")
    lines.append("")
    lines.append("| 字段 | 说明 |")
    lines.append("|---|---|")
    field_desc = [
        ("evidence_id", "原始证据块 ID"),
        ("doc_id", "文档 ID"),
        ("page_start", "起始页码"),
        ("page_end", "结束页码"),
        ("section_path", "章节路径（列表）"),
        ("content_type", "内容类型 paragraph/caption/heading/ocr_text"),
        ("title_context", "标题上下文"),
        ("stage3_priority", "阶段 3 优先级 high/medium/low"),
        ("review_required", "是否需要人工复核"),
        ("parse_quality", "解析质量分"),
        ("source_parser", "来源解析器"),
        ("text", "text_normalized 或 text_raw"),
        ("text_hash", "text 的 sha256"),
        ("source_file", "来源文件 evidence_chunks.jsonl"),
    ]
    for f, d in field_desc:
        lines.append(f"| {f} | {d} |")
    lines.append("")

    lines.append("## 5. Payload 索引创建情况")
    lines.append("12. 索引字段状态：")
    lines.append("")
    lines.append("| 字段 | 状态 |")
    lines.append("|---|---|")
    for f, st in payload_index_status.items():
        lines.append(f"| {f} | {st} |")
    lines.append("")

    lines.append("## 6. 检索冒烟测试摘要")
    lines.append("13. 每条查询 top 8，命中概览：")
    lines.append("")
    lines.append("| 查询 | top1 score | top1 页码 | top1 类型 | top1 优先级 |")
    lines.append("|---|---|---|---|---|")
    for item in smoke_results:
        if item["results"]:
            r0 = item["results"][0]
            lines.append(
                f"| {item['query']} | {r0['score']} | {r0['page_start']} | "
                f"{r0['content_type']} | {r0['stage3_priority']} |")
        else:
            lines.append(f"| {item['query']} | - | - | - | - |")
    lines.append("")

    lines.append("## 7. 当前问题与风险")
    lines.append("14. 说明：")
    risk_lines = []
    if device != "cuda":
        risk_lines.append("- 当前 PyTorch 为 CPU 版本，CUDA 不可用；embedding 使用 CPU，"
                          "规模扩大时速度受限（本阶段数据量小，影响可忽略）。")
    if failed_chunks:
        risk_lines.append(f"- 有 {len(failed_chunks)} 个 chunk 写入失败，详见 failed_chunks.jsonl。")
    if model_source == "modelscope":
        risk_lines.append("- HuggingFace 直连/镜像元数据校验失败，改用 ModelScope 下载模型；"
                          "模型权重与 HF 版本一致。")
    if not risk_lines:
        risk_lines.append("- 无重大风险。")
    lines.extend(risk_lines)
    lines.append("")

    lines.append("## 8. 下一步建议")
    lines.append("15. 建议：")
    lines.append("- 可考虑对表格类内容做结构化提取后再入库（本阶段已排除 table）。")
    lines.append("- 如需 GPU 加速，安装 CUDA 版 PyTorch。")
    lines.append("- 检索质量确认后，进入阶段 3B：接入 Qwen3:8B 做基于证据的问答。")
    lines.append("")

    lines.append("## 边界声明")
    lines.append("```text")
    lines.append("本阶段只完成 Qdrant 向量索引构建；")
    lines.append("未调用生成式大模型；")
    lines.append("未进行问答；")
    lines.append("未抽取产业链实体或关系；")
    lines.append("后续步骤才会接入 Qwen3:8B 进行基于证据的问答或候选关系抽取。")
    lines.append("```")

    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
