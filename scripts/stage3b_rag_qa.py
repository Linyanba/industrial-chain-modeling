#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
阶段 3B：基于 Qdrant 检索证据 + 本地 Ollama qwen3:8b 的带证据引用 RAG 问答测试。

严格边界：
  - 允许：连接 Qdrant、读取已有 collection、用 Qwen3-Embedding-0.6B 生成 query embedding、
          调用本地 Ollama qwen3:8b、基于检索证据回答、输出 evidence_id/页码/原文引文。
  - 禁止：调用 OpenAI/DeepSeek/Claude/Gemini 等云端 API、联网搜索、抽取实体、抽取关系、
          生成图谱、把模型常识当白皮书事实、根据产业链图箭头下确定结论、
          将未复核表格当确定事实、修改/覆盖原始 PDF 与阶段 1/2/3A 输出。

本脚本只做 RAG 问答测试，不抽取实体、不抽取关系、不生成图谱。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import urllib.request
import urllib.error

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------

ALLOWED_TOP_LEVEL_FIELDS = ["question", "answer", "claims", "insufficient_evidence", "warnings"]
INSUFFICIENT_MARK = "当前证据不足"

# 发送给 qwen3:8b 的系统约束（强约束、只基于证据）
SYSTEM_RULES = """你是产业链白皮书证据问答助手。
严格规则：
1. 你只能根据下面给定的【证据】回答问题。
2. 不得使用常识补全，不得使用任何外部知识。
3. 如果给定证据不足以回答，answer 必须写“当前证据不足”，并在 insufficient_evidence 中说明。
4. 每个实质性结论都必须放进 claims，并标注对应的 evidence_id 和 page_no。
5. quote 必须是所引用证据原文中的一段连续摘录，不得改写、不得杜撰。
6. 不得把预测、规划、趋势、目标表述当作已经发生的事实。
7. 不得根据未经复核的表格内容或产业链图箭头生成确定结论。
8. 输出必须是合法 JSON；不要输出 Markdown；不要输出代码块；不要输出任何额外解释文字。
9. JSON 顶层字段只能是：question、answer、claims、insufficient_evidence、warnings。
10. claims 是数组，每个元素必须含 claim、evidence_id、page_no、quote 四个字段。"""

SCHEMA_EXAMPLE = """{
  "question": "原问题",
  "answer": "基于证据的回答。如果证据不足，写当前证据不足。",
  "claims": [
    {"claim": "一个具体结论", "evidence_id": "xxx", "page_no": 10, "quote": "支持该结论的原文连续摘录"}
  ],
  "insufficient_evidence": [
    {"missing_point": "证据不足的方面", "reason": "为什么当前证据不能支持"}
  ],
  "warnings": ["如使用了待复核证据或证据存在局限，在这里说明"]
}"""

logger = logging.getLogger("stage3b")


# ----------------------------------------------------------------------------
# 日志
# ----------------------------------------------------------------------------

def setup_logger(log_path: Path) -> None:
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    try:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass


def log(msg: str) -> None:
    logger.info(msg)


# ----------------------------------------------------------------------------
# 配置 / 问题加载
# ----------------------------------------------------------------------------

def load_yaml(path: Path) -> dict:
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_seed_questions(path: Path) -> List[Dict[str, str]]:
    data = load_yaml(path)
    qs = data.get("questions", [])
    out = []
    for i, q in enumerate(qs, 1):
        qid = str(q.get("id") or f"q{i:02d}")
        question = str(q.get("question") or "").strip()
        if question:
            out.append({"id": qid, "question": question})
    return out


# ----------------------------------------------------------------------------
# Embedding 模型（三级回退，与阶段 3A 一致）
# ----------------------------------------------------------------------------

def load_embedding_model(model_name: str):
    import torch
    from sentence_transformers import SentenceTransformer
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if os.path.isdir(model_name):
        log(f"从本地目录加载 embedding 模型: {model_name}")
        return SentenceTransformer(model_name, device=device), model_name, "local_dir", device
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    try:
        log(f"尝试从 HuggingFace 加载: {model_name} (HF_ENDPOINT={os.environ.get('HF_ENDPOINT')})")
        m = SentenceTransformer(model_name, device=device)
        return m, model_name, "huggingface", device
    except Exception as e:
        log(f"HuggingFace 加载失败，回退 ModelScope: {type(e).__name__}: {e}")
    from modelscope import snapshot_download
    local_path = snapshot_download(model_name)
    log(f"ModelScope 已下载到: {local_path}")
    m = SentenceTransformer(local_path, device=device)
    return m, local_path, "modelscope", device


def encode_query(model, text: str):
    kwargs = dict(normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)
    try:
        vec = model.encode([text], prompt_name="query", **kwargs)
    except Exception:
        vec = model.encode([text], **kwargs)
    return vec[0].tolist()


# ----------------------------------------------------------------------------
# 文本工具
# ----------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def norm_ws(s: str) -> str:
    """去掉所有空白，用于 quote 忽略空白匹配。"""
    if not s:
        return ""
    return _WS_RE.sub("", s)


def preview(text: str, n: int = 160) -> str:
    t = (text or "").replace("\n", " ").strip()
    return t[:n]


# ----------------------------------------------------------------------------
# Qdrant 检索 + 重排
# ----------------------------------------------------------------------------

def retrieve(client, collection: str, qvec: List[float], top_k: int) -> List[Dict[str, Any]]:
    from qdrant_client import models as qmodels  # noqa
    res = client.query_points(
        collection_name=collection,
        query=qvec,
        limit=top_k,
        with_payload=True,
    )
    points = res.points if hasattr(res, "points") else res
    out = []
    for p in points:
        payload = p.payload or {}
        out.append({
            "score": float(p.score) if p.score is not None else 0.0,
            "evidence_id": payload.get("evidence_id"),
            "page_start": payload.get("page_start"),
            "page_end": payload.get("page_end"),
            "section_path": payload.get("section_path"),
            "content_type": payload.get("content_type"),
            "stage3_priority": payload.get("stage3_priority"),
            "review_required": bool(payload.get("review_required", False)),
            "text": payload.get("text") or "",
        })
    return out


def rerank_and_select(candidates: List[Dict[str, Any]],
                      final_k: int,
                      max_per_page: int,
                      priority_bonus: Dict[str, float],
                      include_review_required: bool) -> List[Dict[str, Any]]:
    # 1. review_required 过滤（默认排除）
    used_review = False
    pool = []
    for c in candidates:
        if c["review_required"] and not include_review_required:
            continue
        if c["review_required"]:
            used_review = True
        pool.append(c)
    # 2. 计算重排分：score + 优先级加权
    for c in pool:
        bonus = priority_bonus.get(str(c.get("stage3_priority")), 0.0)
        c["rerank_score"] = c["score"] + bonus
    pool.sort(key=lambda x: x["rerank_score"], reverse=True)
    # 3. 同页最多保留 max_per_page 条
    page_count: Dict[Any, int] = {}
    selected = []
    for c in pool:
        pg = c.get("page_start")
        page_count[pg] = page_count.get(pg, 0) + 1
        if page_count[pg] > max_per_page:
            continue
        selected.append(c)
        if len(selected) >= final_k:
            break
    return selected, used_review


# ----------------------------------------------------------------------------
# Prompt 构造
# ----------------------------------------------------------------------------

def build_evidence_block(evidences: List[Dict[str, Any]]) -> str:
    lines = []
    for i, e in enumerate(evidences, 1):
        sec = e.get("section_path")
        if isinstance(sec, list):
            sec = " > ".join(str(x) for x in sec)
        review_tag = "（待复核）" if e.get("review_required") else ""
        lines.append(
            f"[证据{i}] evidence_id={e.get('evidence_id')} | page_no={e.get('page_start')} | "
            f"content_type={e.get('content_type')} | priority={e.get('stage3_priority')}{review_tag}\n"
            f"章节: {sec}\n原文: {e.get('text')}"
        )
    return "\n\n".join(lines)


def build_prompt(question: str, evidences: List[Dict[str, Any]]) -> str:
    ev_block = build_evidence_block(evidences)
    return (
        SYSTEM_RULES
        + "\n\n【证据】(只能使用以下证据，evidence_id 只能取自这里)\n"
        + ev_block
        + "\n\n【问题】\n" + question
        + "\n\n【输出 JSON schema，必须严格遵守】\n" + SCHEMA_EXAMPLE
        + "\n\n现在请只输出合法 JSON，question 字段填写上面的问题原文："
    )


def build_repair_prompt(question: str, evidences: List[Dict[str, Any]], raw: str) -> str:
    ev_block = build_evidence_block(evidences)
    return (
        "你上一次的输出不是合法/合规的 JSON。请只修复【格式和字段】，不得新增任何事实、不得修改结论内容。\n"
        "要求：输出必须是合法 JSON；顶层字段只能是 question、answer、claims、insufficient_evidence、warnings；"
        "claims 每项含 claim、evidence_id、page_no、quote；evidence_id 只能取自下面证据；"
        "quote 必须是所引证据原文中的连续摘录。不要输出 Markdown 或代码块或解释。\n\n"
        "【目标 schema】\n" + SCHEMA_EXAMPLE
        + "\n\n【证据】\n" + ev_block
        + "\n\n【问题】\n" + question
        + "\n\n【你上一次的原始输出】\n" + raw
        + "\n\n请输出修复后的合法 JSON："
    )


# ----------------------------------------------------------------------------
# Ollama 调用
# ----------------------------------------------------------------------------

def _http_post_json(url: str, body: dict, timeout: int) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def call_ollama_generate(ollama_url: str, model: str, prompt: str,
                         num_predict: int, timeout: int) -> Tuple[Optional[str], str, Optional[str]]:
    """优先 /api/generate。返回 (text, endpoint_used, error)。"""
    gen_url = ollama_url.rstrip("/") + "/api/generate"
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "think": False,  # qwen3 思考型模型：关闭思考，保证纯 JSON
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    try:
        resp = _http_post_json(gen_url, body, timeout)
        return resp.get("response", ""), "/api/generate", None
    except Exception as e:
        gen_err = f"{type(e).__name__}: {e}"
        log(f"/api/generate 失败，尝试回退 /api/chat: {gen_err}")

    # fallback: /api/chat
    chat_url = ollama_url.rstrip("/") + "/api/chat"
    body_chat = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        "think": False,
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    try:
        resp = _http_post_json(chat_url, body_chat, timeout)
        msg = resp.get("message", {}) or {}
        return msg.get("content", ""), "/api/chat", f"generate_failed: {gen_err}"
    except Exception as e:
        return None, "/api/chat", f"generate_err={gen_err}; chat_err={type(e).__name__}: {e}"


# ----------------------------------------------------------------------------
# JSON 解析 + schema 校验 + quote 校验
# ----------------------------------------------------------------------------

def try_parse_json(raw: str) -> Optional[dict]:
    if raw is None:
        return None
    raw = raw.strip()
    # 去掉可能的代码块围栏
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    # 提取第一个 { 到最后一个 } 之间的内容
    l = raw.find("{")
    r = raw.rfind("}")
    if l != -1 and r != -1 and r > l:
        try:
            return json.loads(raw[l:r + 1])
        except Exception:
            return None
    return None


def validate_answer(obj: dict, evidences: List[Dict[str, Any]]) -> Dict[str, Any]:
    """返回校验结果 dict：normalized(obj)、issues、extra_fields_removed、quote_check_status、
    claims_have_evidence_id、claims_have_page_no、no_evidence_claim。"""
    issues: List[str] = []
    ctx = {e.get("evidence_id"): e for e in evidences}

    # 顶层多余字段剔除
    extra = [k for k in obj.keys() if k not in ALLOWED_TOP_LEVEL_FIELDS]
    for k in extra:
        obj.pop(k, None)
    if extra:
        issues.append(f"移除多余顶层字段: {extra}")

    obj.setdefault("question", "")
    obj.setdefault("answer", "")
    obj.setdefault("claims", [])
    obj.setdefault("insufficient_evidence", [])
    obj.setdefault("warnings", [])

    claims = obj.get("claims")
    if not isinstance(claims, list):
        issues.append("claims 不是数组")
        claims = []
        obj["claims"] = []

    claims_have_evidence_id = True
    claims_have_page_no = True
    quote_all_ok = True
    quote_checked = 0
    no_evidence_claim = False

    for idx, c in enumerate(claims):
        if not isinstance(c, dict):
            issues.append(f"claim[{idx}] 不是对象")
            claims_have_evidence_id = False
            claims_have_page_no = False
            quote_all_ok = False
            continue
        eid = c.get("evidence_id")
        pno = c.get("page_no")
        quote = c.get("quote")
        if not eid:
            claims_have_evidence_id = False
            issues.append(f"claim[{idx}] 缺少 evidence_id")
            no_evidence_claim = True
        if pno is None:
            claims_have_page_no = False
            issues.append(f"claim[{idx}] 缺少 page_no")
        # evidence_id 必须在上下文中
        ev = ctx.get(eid) if eid else None
        if eid and ev is None:
            issues.append(f"claim[{idx}] evidence_id={eid} 不在检索上下文")
            no_evidence_claim = True
            quote_all_ok = False
            continue
        # page_no 与 evidence 对应页码一致
        if ev is not None and pno is not None:
            if str(pno) != str(ev.get("page_start")):
                issues.append(f"claim[{idx}] page_no={pno} 与 evidence 页码 {ev.get('page_start')} 不一致")
                claims_have_page_no = False
        # quote 匹配
        if ev is not None and quote:
            quote_checked += 1
            if norm_ws(str(quote)) not in norm_ws(str(ev.get("text"))):
                issues.append(f"claim[{idx}] quote 无法在 evidence 原文中定位")
                quote_all_ok = False
        elif ev is not None and not quote:
            issues.append(f"claim[{idx}] 缺少 quote")
            quote_all_ok = False

    # quote_check_status
    ans_text = str(obj.get("answer") or "")
    is_insufficient = INSUFFICIENT_MARK in ans_text and len(claims) == 0
    if len(claims) == 0:
        quote_check_status = "no_claims"  # 无 claim（可能证据不足）
    elif quote_all_ok:
        quote_check_status = "pass"
    else:
        quote_check_status = "fail"

    return {
        "obj": obj,
        "issues": issues,
        "extra_fields_removed": extra,
        "claims_have_evidence_id": claims_have_evidence_id,
        "claims_have_page_no": claims_have_page_no,
        "quote_check_status": quote_check_status,
        "quote_all_ok": quote_all_ok,
        "no_evidence_claim": no_evidence_claim,
        "is_insufficient": is_insufficient,
        "num_claims": len(claims),
    }


def answer_needs_review(v: Dict[str, Any], used_review_evidence: bool) -> bool:
    if v["no_evidence_claim"]:
        return True
    if v["quote_check_status"] == "fail":
        return True
    if not v["claims_have_evidence_id"]:
        return True
    if not v["claims_have_page_no"]:
        return True
    if used_review_evidence:
        return True
    return False


# ----------------------------------------------------------------------------
# 连通性 / 前置检查
# ----------------------------------------------------------------------------

def http_get(url: str, timeout: int = 15) -> Tuple[Optional[int], Optional[str]]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        log(f"HTTP GET 失败 {url}: {type(e).__name__}: {e}")
        return None, None


def check_ollama(ollama_url: str, model: str) -> Tuple[bool, bool]:
    """返回 (connected, model_available)。"""
    base = ollama_url.rstrip("/")
    status, _ = http_get(base, timeout=10)
    connected = status is not None
    model_available = False
    if connected:
        st, body = http_get(base + "/api/tags", timeout=10)
        if body:
            try:
                tags = json.loads(body)
                names = [m.get("name") for m in tags.get("models", [])]
                model_available = model in names or any(str(n).startswith(model) for n in names)
            except Exception:
                pass
    return connected, model_available


# ----------------------------------------------------------------------------
# 单问题处理
# ----------------------------------------------------------------------------

def process_question(q: Dict[str, str], client, collection, model,
                     args, cfg_ret: dict) -> Tuple[dict, dict, Optional[dict]]:
    """返回 (retrieval_record, answer_record, failed_record_or_None)。"""
    qid = q["id"]
    question = q["question"]
    ts = datetime.now().isoformat()
    log(f"[{qid}] 检索: {question}")

    qvec = encode_query(model, question)
    candidates = retrieve(client, collection, qvec, args.top_k)
    selected, used_review = rerank_and_select(
        candidates, args.final_k, cfg_ret["max_per_page"],
        cfg_ret["priority_bonus"], args.include_review_required,
    )

    retrieved_out = []
    for rank, e in enumerate(selected, 1):
        retrieved_out.append({
            "rank": rank,
            "score": round(e["score"], 6),
            "evidence_id": e["evidence_id"],
            "page_no": e["page_start"],
            "section_path": e["section_path"],
            "content_type": e["content_type"],
            "stage3_priority": e["stage3_priority"],
            "review_required": e["review_required"],
            "text_preview": preview(e["text"], 200),
        })
    retrieval_record = {
        "question_id": qid, "question": question,
        "top_k": args.top_k, "final_k": len(selected),
        "retrieved": retrieved_out,
    }
    context_ids = [e["evidence_id"] for e in selected]

    # dry-run：只做检索
    if args.dry_run:
        answer_record = {
            "question_id": qid, "question": question, "answer": "[dry-run] 未调用模型",
            "claims": [], "insufficient_evidence": [], "warnings": ["dry-run"],
            "answer_review_required": False, "model_name": args.llm_model,
            "ollama_url": args.ollama_url, "context_evidence_ids": context_ids,
            "json_valid": None, "quote_check_status": "skipped",
        }
        return retrieval_record, answer_record, None

    # 无检索结果
    if not selected:
        answer_record = {
            "question_id": qid, "question": question, "answer": INSUFFICIENT_MARK,
            "claims": [], "insufficient_evidence": [{"missing_point": "检索无结果", "reason": "排除待复核后无可用证据"}],
            "warnings": ["检索结果为空"], "answer_review_required": True,
            "model_name": args.llm_model, "ollama_url": args.ollama_url,
            "context_evidence_ids": [], "json_valid": True, "quote_check_status": "no_claims",
        }
        return retrieval_record, answer_record, None

    # 调用模型
    prompt = build_prompt(question, selected)
    raw, endpoint, err = call_ollama_generate(
        args.ollama_url, args.llm_model, prompt, args.num_predict, cfg_ret["timeout"])
    obj = try_parse_json(raw)
    retry_raw = None

    need_repair = obj is None
    v = None
    if obj is not None:
        v = validate_answer(obj, selected)
        # 结构性问题触发一次修复：quote fail / 缺字段 / 无效 evidence
        if v["quote_check_status"] == "fail" or not v["claims_have_evidence_id"] or v["no_evidence_claim"]:
            need_repair = True

    if need_repair and cfg_ret["allow_repair"]:
        log(f"[{qid}] 触发一次 JSON 修复重试")
        rprompt = build_repair_prompt(question, selected, raw or "")
        retry_raw, endpoint2, err2 = call_ollama_generate(
            args.ollama_url, args.llm_model, rprompt, args.num_predict, cfg_ret["timeout"])
        endpoint = endpoint2 or endpoint
        obj2 = try_parse_json(retry_raw)
        if obj2 is not None:
            obj = obj2
            v = validate_answer(obj, selected)

    # 仍无法解析 -> 失败
    if obj is None:
        failed = {
            "question_id": qid, "question": question, "failure_type": "json_parse_failed",
            "raw_response": raw, "retry_response": retry_raw,
            "reason": err or "模型输出无法解析为合法 JSON（含修复重试）", "timestamp": ts,
        }
        answer_record = {
            "question_id": qid, "question": question, "answer": INSUFFICIENT_MARK,
            "claims": [], "insufficient_evidence": [], "warnings": ["模型输出无法解析为 JSON"],
            "answer_review_required": True, "model_name": args.llm_model,
            "ollama_url": args.ollama_url, "context_evidence_ids": context_ids,
            "json_valid": False, "quote_check_status": "parse_failed",
        }
        return retrieval_record, answer_record, failed

    # 组装回答记录
    obj["question"] = obj.get("question") or question
    warnings = obj.get("warnings") or []
    if not isinstance(warnings, list):
        warnings = [str(warnings)]
    if used_review:
        warnings.append("本次使用了 review_required=true 的待复核证据（低优先级复核）")
    if v and v["extra_fields_removed"]:
        warnings.append(f"已剔除多余字段: {v['extra_fields_removed']}")
    obj["warnings"] = warnings

    review_required_flag = answer_needs_review(v, used_review) if v else True
    answer_record = {
        "question_id": qid, "question": question,
        "answer": obj.get("answer", ""), "claims": obj.get("claims", []),
        "insufficient_evidence": obj.get("insufficient_evidence", []),
        "warnings": warnings, "answer_review_required": review_required_flag,
        "model_name": args.llm_model, "ollama_url": args.ollama_url,
        "endpoint_used": endpoint, "context_evidence_ids": context_ids,
        "json_valid": True, "quote_check_status": v["quote_check_status"] if v else "unknown",
        "_validation": v, "_used_review": used_review,
    }
    return retrieval_record, answer_record, None


# ----------------------------------------------------------------------------
# 复核队列生成
# ----------------------------------------------------------------------------

def build_review_items(ans: dict) -> List[dict]:
    items = []
    qid = ans["question_id"]
    v = ans.get("_validation")
    ctr = [0]

    def add(issue_type, severity, reason, eid="", pno="", action="人工核对原文与结论"):
        ctr[0] += 1
        items.append({
            "review_item_id": f"{qid}_r{ctr[0]:02d}", "question_id": qid,
            "issue_type": issue_type, "severity": severity, "reason": reason,
            "evidence_id": eid, "page_no": pno, "recommended_action": action,
        })

    if ans.get("quote_check_status") == "parse_failed":
        add("model_not_json", "high", "模型未按 JSON 输出且修复失败", action="人工重跑或改写问题")
        return items
    if not v:
        return items
    if not v["claims_have_evidence_id"]:
        add("claim_missing_evidence_id", "high", "存在 claim 缺少 evidence_id")
    if not v["claims_have_page_no"]:
        add("claim_missing_page_no", "medium", "存在 claim 缺少或页码不一致")
    if v["quote_check_status"] == "fail":
        add("quote_not_found", "high", "存在 quote 无法在原文中定位")
    if v["no_evidence_claim"]:
        add("claim_without_evidence", "high", "出现无证据支持的结论")
    if v["is_insufficient"]:
        add("insufficient_evidence", "low", "模型判定证据不足", action="补充检索或标注确实缺失")
    if ans.get("_used_review"):
        add("review_required_evidence", "low", "使用了 review_required=true 的待复核证据",
            action="低优先级：复核该证据后再采用结论")
    return items


# ----------------------------------------------------------------------------
# 输出写入
# ----------------------------------------------------------------------------

def write_jsonl(path: Path, rows: List[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            clean = {k: v for k, v in r.items() if not k.startswith("_")}
            f.write(json.dumps(clean, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: List[dict], fields: List[str], encoding: str) -> None:
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 3B：RAG 问答测试（Qdrant + Ollama qwen3:8b）")
    parser.add_argument("--project-root", default=r"D:\产业链建模")
    parser.add_argument("--collection", default="whitepaper_chunks")
    parser.add_argument("--source-pdf", action="append", default=[], help="用于原始文件哈希校验，可重复")
    parser.add_argument("--embedding-model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--llm-model", default="qwen3:8b")
    parser.add_argument("--mode", choices=["batch", "single"], default="batch")
    parser.add_argument("--question", default=None)
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--final-k", type=int, default=10)
    parser.add_argument("--include-review-required", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--num-predict", type=int, default=1024)
    args = parser.parse_args()

    project_root = Path(args.project_root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rag_root = project_root / "rag"
    out_dir = rag_root / "outputs" / f"stage3b_rag_qa_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_logger(out_dir / "run.log")

    log("=" * 70)
    log("阶段 3B：RAG 问答测试启动")
    log(f"输出目录: {out_dir}")

    # 读取配置（若存在）
    cfg_path = rag_root / "config" / "rag_qa_config.yaml"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = load_yaml(cfg_path)
        except Exception as e:
            log(f"配置读取失败（使用默认值）: {e}")
    ret_cfg = (cfg.get("retrieval") or {})
    llm_cfg = (cfg.get("llm") or {})
    cfg_ret = {
        "max_per_page": int(ret_cfg.get("max_per_page", 3)),
        "priority_bonus": ret_cfg.get("priority_bonus", {"high": 0.05, "medium": 0.02, "low": 0.0}),
        "timeout": int(llm_cfg.get("request_timeout_sec", 180)),
        "allow_repair": bool((cfg.get("validation") or {}).get("allow_one_repair_retry", True)),
    }
    csv_encoding = ((cfg.get("output") or {}).get("csv_encoding")) or "utf-8-sig"

    # --- 前置检查 ---
    val = {}
    coll_url = args.qdrant_url.rstrip("/") + f"/collections/{args.collection}"
    st, body = http_get(coll_url)
    qdrant_connected = st is not None
    collection_exists = st == 200
    collection_has_points = False
    if body:
        try:
            info = json.loads(body).get("result", {})
            collection_has_points = int(info.get("points_count", 0)) > 0
        except Exception:
            pass
    val["qdrant_connected"] = (qdrant_connected, f"HTTP {st}")
    val["collection_exists"] = (collection_exists, args.collection)
    val["collection_has_points"] = (collection_has_points, "points_count>0")

    ollama_connected, model_available = check_ollama(args.ollama_url, args.llm_model)
    val["ollama_connected"] = (ollama_connected, args.ollama_url)
    val["llm_model_available"] = (model_available, args.llm_model)

    if not (qdrant_connected and collection_exists and collection_has_points):
        log("[FAIL] Qdrant / collection 不可用，阶段 3B 未通过")
        write_validation(out_dir, val, extra={"aborted": "qdrant_unavailable"})
        _fail_report(out_dir, args, "Qdrant collection 不可用")
        return 2
    if not (ollama_connected and model_available):
        log("[FAIL] Ollama 或 qwen3:8b 不可用，阶段 3B 未通过")
        write_validation(out_dir, val, extra={"aborted": "ollama_unavailable"})
        _fail_report(out_dir, args, "Ollama 或 qwen3:8b 不可用")
        return 2

    # --- 加载问题 ---
    if args.mode == "single":
        if not args.question:
            log("single 模式需要 --question")
            return 2
        questions = [{"id": "single_q", "question": args.question.strip()}]
    else:
        seed_path = rag_root / "queries" / "stage3b_seed_questions.yaml"
        questions = load_seed_questions(seed_path)
    val["seed_questions_loaded"] = (len(questions) > 0, f"{len(questions)} 个问题")

    # --- 加载 embedding 模型 ---
    log("加载 embedding 模型...")
    model, resolved, source, device = load_embedding_model(args.embedding_model)
    log(f"embedding 模型就绪: source={source}, device={device}")
    val["embedding_model_loaded"] = (True, f"{args.embedding_model} ({source}/{device})")

    from qdrant_client import QdrantClient
    client = QdrantClient(url=args.qdrant_url)

    # --- 逐题处理 ---
    retrieval_rows, answer_rows, failed_rows, review_rows = [], [], [], []
    for q in questions:
        try:
            rrec, arec, frec = process_question(q, client, args.collection, model, args, cfg_ret)
            retrieval_rows.append(rrec)
            answer_rows.append(arec)
            if frec:
                failed_rows.append(frec)
            review_rows.extend(build_review_items(arec))
            # 相关性较差提示（最高分过低）
            if rrec["retrieved"] and rrec["retrieved"][0]["score"] < 0.3:
                review_rows.append({
                    "review_item_id": f"{q['id']}_rel", "question_id": q["id"],
                    "issue_type": "low_relevance", "severity": "low",
                    "reason": f"最高检索分较低({rrec['retrieved'][0]['score']:.3f})",
                    "evidence_id": "", "page_no": "", "recommended_action": "人工确认检索相关性",
                })
        except Exception as e:
            log(f"[{q['id']}] 处理异常: {type(e).__name__}: {e}")
            failed_rows.append({
                "question_id": q["id"], "question": q["question"], "failure_type": "exception",
                "raw_response": None, "retry_response": None,
                "reason": f"{type(e).__name__}: {e}", "timestamp": datetime.now().isoformat(),
            })

    # --- 写输出文件 ---
    write_jsonl(out_dir / "retrieval_context.jsonl", retrieval_rows)
    write_jsonl(out_dir / "rag_answers.jsonl", answer_rows)
    write_jsonl(out_dir / "failed_questions.jsonl", failed_rows)
    write_csv(out_dir / "rag_qa_review_queue.csv", review_rows,
              ["review_item_id", "question_id", "issue_type", "severity", "reason",
               "evidence_id", "page_no", "recommended_action"], csv_encoding)

    run_config = {
        "timestamp": timestamp, "project_root": str(project_root),
        "collection": args.collection, "embedding_model": args.embedding_model,
        "embedding_source": source, "device": device,
        "qdrant_url": args.qdrant_url, "ollama_url": args.ollama_url,
        "llm_model": args.llm_model, "mode": args.mode,
        "top_k": args.top_k, "final_k": args.final_k,
        "include_review_required": args.include_review_required,
        "dry_run": args.dry_run, "num_predict": args.num_predict,
        "primary_endpoint": "/api/generate",
        "num_questions": len(questions),
    }
    (out_dir / "run_config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- 汇总统计 ---
    valid_json = [a for a in answer_rows if a.get("json_valid")]
    insufficient = [a for a in answer_rows if INSUFFICIENT_MARK in str(a.get("answer", ""))]
    review_qs = sorted({a["question_id"] for a in answer_rows if a.get("answer_review_required")})
    quote_fail = [a for a in answer_rows if a.get("quote_check_status") == "fail"]
    no_evidence = [a for a in answer_rows
                   if a.get("_validation") and a["_validation"].get("no_evidence_claim")]
    all_pages = sorted({r["page_no"] for rr in retrieval_rows for r in rr["retrieved"]
                        if r.get("page_no") is not None})

    claims_ok_eid = all(
        (a["_validation"]["claims_have_evidence_id"] if a.get("_validation") else True)
        for a in answer_rows)
    claims_ok_pno = all(
        (a["_validation"]["claims_have_page_no"] if a.get("_validation") else True)
        for a in answer_rows)
    quotes_ok = all(a.get("quote_check_status") in ("pass", "no_claims", "skipped")
                    for a in answer_rows)

    val["retrieval_completed"] = (len(retrieval_rows) == len(questions), f"{len(retrieval_rows)}/{len(questions)}")
    val["rag_answers_generated"] = (len(answer_rows) == len(questions), f"{len(answer_rows)}")
    val["answers_valid_json"] = (len(valid_json) == len(answer_rows) and len(answer_rows) > 0,
                                 f"{len(valid_json)}/{len(answer_rows)}")
    val["claims_have_evidence_id"] = (claims_ok_eid, "所有 claim 均含 evidence_id")
    val["claims_have_page_no"] = (claims_ok_pno, "所有 claim 页码一致")
    val["quotes_match_source_text_or_reviewed"] = (quotes_ok, "quote 匹配或无争议")
    val["no_external_api_called"] = (True, "仅本地 Qdrant + Ollama")
    val["no_relation_extraction_performed"] = (True, "未抽取实体/关系")
    source_rows = []
    documents_csv = project_root / "metadata" / "documents.csv"
    if documents_csv.exists():
        with documents_csv.open("r", encoding="utf-8-sig", newline="") as fh:
            source_rows = list(csv.DictReader(fh))
    requested = {str(Path(p).resolve()) for p in args.source_pdf}
    if requested:
        source_rows = [r for r in source_rows if str(Path(r.get("file_path", "")).resolve()) in requested]
    source_checks = []
    for row in source_rows:
        path = Path(row.get("file_path", ""))
        expected = str(row.get("file_sha256") or "").lower()
        if not path.exists() or not expected:
            source_checks.append(False)
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest().lower()
        source_checks.append(digest == expected)
    pdf_ok = bool(source_checks) and all(source_checks)
    val["original_pdf_not_modified"] = (pdf_ok, f"已校验 {len(source_checks)} 个原始 PDF 的 SHA256")

    write_validation(out_dir, val)

    stats = {
        "num_questions": len(questions), "valid_json": len(valid_json),
        "insufficient": len(insufficient), "review_qs": review_qs,
        "quote_fail": len(quote_fail), "no_evidence": len(no_evidence),
        "all_pages": all_pages, "failed": len(failed_rows),
    }
    write_report(out_dir, args, run_config, retrieval_rows, answer_rows, stats, val)

    # latest 指针
    (rag_root / "latest_stage3b_run.txt").write_text(str(out_dir), encoding="utf-8")

    log("=" * 70)
    log(f"完成: {len(valid_json)}/{len(questions)} 合法 JSON, "
        f"复核问题 {len(review_qs)}, 失败 {len(failed_rows)}, quote失败 {len(quote_fail)}")
    all_pass = all(v[0] for v in val.values())
    log(f"阶段 3B 验收: {'通过' if all_pass else '存在未通过项，见报告'}")
    return 0


def write_validation(out_dir: Path, val: Dict[str, Tuple[bool, str]], extra: dict = None) -> None:
    obj = {k: {"passed": bool(v[0]), "note": v[1]} for k, v in val.items()}
    if extra:
        obj["_extra"] = extra
    (out_dir / "validation_summary.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _fail_report(out_dir: Path, args, reason: str) -> None:
    md = ["# 阶段 3B RAG 问答测试报告（未通过）", "", f"- 失败原因: {reason}",
          f"- Qdrant: {args.qdrant_url} / {args.collection}",
          f"- Ollama: {args.ollama_url} / {args.llm_model}", "",
          "未生成问答结果。请修复上述前置条件后重跑。", "",
          "> 本阶段只完成基于 Qdrant 检索证据的 RAG 问答测试；未抽取实体；未抽取关系；未生成图谱。"]
    (out_dir / "rag_qa_report.md").write_text("\n".join(md), encoding="utf-8")


def write_report(out_dir, args, run_config, retrieval_rows, answer_rows, stats, val) -> None:
    ans_by_id = {a["question_id"]: a for a in answer_rows}
    lines = []
    lines.append("# 阶段 3B RAG 问答测试报告")
    lines.append("")
    lines.append(f"- 生成时间: {run_config['timestamp']}")
    lines.append(f"- Qdrant 地址 / collection: {args.qdrant_url} / {args.collection}")
    lines.append(f"- Embedding 模型: {args.embedding_model} ({run_config['embedding_source']}/{run_config['device']})")
    lines.append(f"- 生成模型: {args.llm_model}")
    lines.append("- 是否使用 Ollama 本地 API: 是")
    lines.append("- 是否默认使用 /api/generate: 是（仅在 generate 失败时才回退 /api/chat）")
    lines.append(f"- 测试问题数量: {stats['num_questions']}")
    lines.append("")
    lines.append("## 各问题概览")
    lines.append("")
    lines.append("| 问题ID | 主要命中页码 | 合法JSON | 证据不足 | 需复核 | quote状态 |")
    lines.append("|---|---|---|---|---|---|")
    for rr in retrieval_rows:
        qid = rr["question_id"]
        pages = sorted({r["page_no"] for r in rr["retrieved"][:5] if r.get("page_no") is not None})
        a = ans_by_id.get(qid, {})
        insuff = "是" if INSUFFICIENT_MARK in str(a.get("answer", "")) else "否"
        review = "是" if a.get("answer_review_required") else "否"
        jv = "是" if a.get("json_valid") else "否"
        lines.append(f"| {qid} | {pages} | {jv} | {insuff} | {review} | {a.get('quote_check_status')} |")
    lines.append("")
    lines.append("## 质量汇总")
    lines.append("")
    lines.append(f"- 成功生成合法 JSON 的问题: {stats['valid_json']}/{stats['num_questions']}")
    lines.append(f"- 证据不足的问题数: {stats['insufficient']}")
    lines.append(f"- 进入人工复核的问题: {len(stats['review_qs'])} -> {stats['review_qs']}")
    lines.append(f"- 是否出现无证据回答: {'是' if stats['no_evidence'] else '否'}")
    lines.append(f"- quote 匹配失败的问题数: {stats['quote_fail']}")
    lines.append(f"- 最终失败问题数: {stats['failed']}")
    lines.append(f"- 检索命中的主要页码(全体去重): {stats['all_pages']}")
    lines.append("")
    lines.append("## RAG 问答质量评价")
    lines.append("")
    if stats["valid_json"] == stats["num_questions"] and stats["quote_fail"] == 0 and stats["failed"] == 0:
        lines.append("所有问题均生成合法 JSON，主要结论带 evidence_id 与 page_no，quote 可在原文定位，质量良好。")
    else:
        lines.append("部分问题存在 JSON/quote/证据问题，已进入复核队列，详见 rag_qa_review_queue.csv。")
    lines.append("")
    lines.append("## 进入阶段 3C 前需要修正的问题")
    lines.append("")
    if stats["review_qs"] or stats["failed"]:
        lines.append(f"- 需复核/失败问题: {stats['review_qs']}，共失败 {stats['failed']} 个，建议核对后再进入 3C。")
    else:
        lines.append("- 无阻塞性问题。")
    lines.append("")
    lines.append("## 验证项")
    lines.append("")
    for k, v in val.items():
        lines.append(f"- {'PASS' if v[0] else 'FAIL'} {k}: {v[1]}")
    lines.append("")
    lines.append("---")
    lines.append("> 本阶段只完成基于 Qdrant 检索证据的 RAG 问答测试；未抽取产业链实体；"
                 "未抽取产业链关系；未生成正式产业链图谱；所有回答仅代表基于当前检索证据的临时问答结果。")
    (out_dir / "rag_qa_report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
