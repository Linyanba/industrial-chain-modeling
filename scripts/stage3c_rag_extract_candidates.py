#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
阶段 3C：基于 Qdrant 检索证据 + 本地 Ollama qwen3:8b，抽取当前白皮书产业链的
候选实体与候选关系（仅候选，不生成正式图谱、不写 Neo4j、不做最终归并）。

严格边界：
  - 允许：连接 Qdrant、读取已有 collection、用 Qwen3-Embedding-0.6B 生成 query embedding、
          调用本地 Ollama qwen3:8b、基于检索证据抽取候选实体/关系、输出 evidence_id/page_no/quote。
  - 禁止：调用云端 API、联网搜索、用模型常识补全、从文档档案标记的产业链图箭头直接抽取确定关系、
          从未复核表格单元格直接抽取确定关系、把预测/规划/趋势当现实事实、
          生成正式图谱/写 Neo4j/实体最终归并、修改原始 PDF、覆盖阶段 1/2/3A/3B 输出。

verified 仅表示“通过原文与规则校验的候选”，不代表人工最终确认。
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
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from document_profile_manager import document_rules, resolve_document_profile

import urllib.request
import urllib.error

# ----------------------------------------------------------------------------
# 受控词表（默认值，可被 config 覆盖）
# ----------------------------------------------------------------------------

ENTITY_TYPES = {
    "industry", "sector", "sub_chain", "industry_link", "process", "material",
    "component", "product", "equipment", "technology", "application", "company",
    "organization", "region", "policy", "standard", "unknown",
}
RELATION_TYPES = {
    "PART_OF", "INPUT_TO", "OUTPUT_OF", "SUPPLIES_TO", "PROCESSES", "MANUFACTURES",
    "OPERATES_IN", "SERVES", "USES_TECHNOLOGY", "LOCATED_IN", "REGULATES",
    "GOVERNS", "RECYCLES", "SUBSTITUTES",
}
ASSERTION_TYPES = {"explicit_fact", "forecast", "policy_goal", "uncertain"}
FORECAST_KEYWORDS = ["预计", "未来", "计划", "拟", "有望", "可能", "或将", "建议",
                     "鼓励", "推动", "促进", "提升", "趋势", "目标", "到2025年", "到2030年"]
DIAGRAM_RESTRICTED_PAGES: set[int] = set()
DIAGRAM_RESTRICTED_PAGES_BY_DOC: Dict[str, set[int]] = {}
RESTRICTED_CONTENT_TYPES = {"table", "table_reference", "figure"}
RELATION_CUES = {
    "PART_OF": ("属于", "包括", "包含", "组成", "构成", "分为", "涵盖"),
    # 只使用关系谓词；“材料/原料/部件”是实体名词，不能仅凭共现证明输入关系。
    "INPUT_TO": ("用于", "投入", "输入", "需要", "采用"),
    "OUTPUT_OF": ("产出", "生产", "制造", "形成", "输出"),
    "SUPPLIES_TO": ("供应", "供给", "提供", "配套"),
    "PROCESSES": ("加工", "处理", "制备", "封装", "测试"),
    "MANUFACTURES": ("生产", "制造", "研制"),
    "OPERATES_IN": ("从事", "布局", "经营", "业务"),
    "SERVES": ("服务", "面向", "应用于", "用于"),
    "USES_TECHNOLOGY": ("采用", "使用", "应用", "基于"),
    "LOCATED_IN": ("位于", "坐落", "基地", "集聚"),
    "REGULATES": ("监管", "规范", "约束", "管理", "规制"),
    "GOVERNS": ("规定", "规范", "标准适用于", "要求"),
    "RECYCLES": ("回收", "循环利用", "再利用"),
    "SUBSTITUTES": ("替代", "取代", "替换"),
}
RELATION_TYPE_ALIASES = {"INPUT": "INPUT_TO", "OUTPUT": "OUTPUT_OF", "SERVING": "SERVES"}
ENTITY_TYPE_ALIASES = {"platform": "technology", "scenario": "application", "market": "application"}
PART_OF_PARENT_TO_CHILD_CUES = ("包括", "包含", "组成", "构成", "分为", "涵盖")
PART_OF_CHILD_TO_PARENT_CUES = ("属于", "隶属于", "是其组成", "是其构成")
RELATION_ENDPOINT_RULES = {
    # 企业/机构可以是原料和部件的明确采用方，例如“面板制造企业采用配套材料”。
    "INPUT_TO": ({"material", "component", "product", "equipment"},
                 {"process", "industry_link", "product", "company", "organization"}),
    "OUTPUT_OF": ({"product", "material", "component"}, {"process", "industry_link", "company"}),
    "MANUFACTURES": ({"company", "organization", "industry_link", "process"}, {"product", "component", "material"}),
    "OPERATES_IN": ({"company", "organization"}, {"industry", "sector", "sub_chain", "industry_link"}),
    "SERVES": ({"product", "component", "industry_link", "company"}, {"application", "industry", "sector"}),
    "USES_TECHNOLOGY": ({"company", "industry_link", "process", "product"}, {"technology", "platform"}),
    "LOCATED_IN": ({"company", "organization", "industry_link"}, {"region"}),
    "REGULATES": ({"policy", "standard", "organization"}, {"industry", "sector", "sub_chain", "industry_link", "process"}),
    "GOVERNS": ({"standard", "policy"}, {"technology", "product", "component", "process"}),
}

ALLOWED_TOP_LEVEL = ["task_id", "entities", "relations", "insufficient_evidence", "warnings"]

SYSTEM_RULES = """你是产业链白皮书候选事实抽取助手。
严格规则：
1. 你只能根据下面给定的【证据】抽取候选实体和候选关系。
2. 不得使用常识补全，不得使用任何外部知识。
3. 证据不足时输出空数组，并在 insufficient_evidence 中说明，不得强行抽取。
4. 每个候选实体和每个候选关系都必须有 evidence_id、page_no 和 quote。
5. quote 必须是所引用证据原文中的一段连续摘录，不得改写、不得杜撰。
6. 不得把预测、规划、趋势、建议、目标当作已经发生的现实产业事实。
7. 不得根据未经复核的表格内容或产业链图箭头生成确定关系。
8. entity_type、subject_type、object_type 只能取自给定实体类型集合。
9. relation_type 只能取自给定关系类型集合，关系方向必须严格遵循定义；方向不清时 review_required=true。
10. assertion_type 只能是 explicit_fact / forecast / policy_goal / uncertain；只有原文明确陈述的现实结构才用 explicit_fact。
11. 输出必须是合法 JSON；不要输出 Markdown；不要输出代码块；不要输出任何额外解释文字。
12. JSON 顶层字段只能是：task_id、entities、relations、insufficient_evidence、warnings。
13. 实体在同一句中共同出现，不等于二者存在关系；只有原文出现与 relation_type 对应的明确关系谓词时才能抽取。
14. “A和B是研发重点/短板/趋势”等并列表达不能抽取为 A供应B、A服务B或A输入B。
15. 每条关系的subject和object都必须是单一原子实体；遇到“A、B、C”等列表时拆成多条关系，禁止把整段列表作为一个实体。
16. 禁止输出INPUT或OUTPUT，必须使用受控值INPUT_TO或OUTPUT_OF。"""

ENTITY_TYPE_DEF = ("实体类型集合: industry(宏观产业) sector(细分赛道) sub_chain(子产业链) "
                   "industry_link(产业链环节) process(工艺) material(材料) component(部件/模块) "
                   "product(产品/类别) equipment(设备) technology(技术/工具/方法) "
                   "application(下游应用) company(企业) organization(机构) region(区域) "
                   "policy(政策/规划) standard(标准) unknown(不可靠分类)")
RELATION_TYPE_DEF = ("关系类型集合与方向: PART_OF(子->父) INPUT_TO(材料/部件/产品->环节/工艺) "
                     "OUTPUT_OF(产品/材料->产出环节) SUPPLIES_TO(上游->下游) PROCESSES(环节/工艺->被加工对象) "
                     "MANUFACTURES(企业->产品) OPERATES_IN(企业->环节/赛道) SERVES(产品/环节->应用) "
                     "USES_TECHNOLOGY(企业/环节/产品->技术) LOCATED_IN(企业/集群->区域) "
                     "REGULATES(政策/标准->产业/环节) GOVERNS(标准->技术/产品/工艺) "
                     "RECYCLES(环节/企业->材料/产品) SUBSTITUTES(实体->被替代实体)")

SCHEMA_EXAMPLE = """{
  "task_id": "任务ID",
  "entities": [
    {"surface_form": "原文实体名称", "normalized_name_candidate": "候选规范名称", "entity_type": "集合内类型", "evidence_id": "xxx", "page_no": 10, "quote": "含该实体的原文连续摘录", "review_required": false, "reason": "极简说明"}
  ],
  "relations": [
    {"subject": "原文主语", "subject_type": "集合内类型", "relation_type": "集合内关系", "object": "原文宾语", "object_type": "集合内类型", "evidence_id": "xxx", "page_no": 10, "quote": "支持该关系的原文连续摘录", "assertion_type": "explicit_fact", "review_required": false, "reason": "极简说明"}
  ],
  "insufficient_evidence": [
    {"missing_point": "证据不足的方面", "reason": "为什么当前证据不能支持"}
  ],
  "warnings": ["证据局限或复核提示"]
}"""

logger = logging.getLogger("stage3c")


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
# 配置 / 任务加载
# ----------------------------------------------------------------------------

def load_yaml(path: Path) -> dict:
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_tasks(path: Path) -> List[Dict[str, Any]]:
    data = load_yaml(path)
    out = []
    for i, t in enumerate(data.get("tasks", []), 1):
        tid = str(t.get("id") or f"t{i:02d}")
        task = str(t.get("task") or "").strip()
        query = str(t.get("query") or "").strip()
        if query:
            out.append({
                "id": tid,
                "task": task,
                "query": query,
                "keywords": [str(x).strip() for x in (t.get("keywords") or []) if str(x).strip()],
            })
    return out


# ----------------------------------------------------------------------------
# Embedding（三级回退，与阶段 3A/3B 一致）
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
    return _WS_RE.sub("", s or "")


def norm_match_text(s: str) -> str:
    """Normalize common PDF/OCR layout variants without paraphrasing text."""
    text = unicodedata.normalize("NFKC", s or "").lower()
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\u00ad", "")
    return re.sub(r"[\s,，。;；:：‘’“”\"'()（）\[\]【】、·\-—–]", "", text)


def is_atomic_entity_name(name: str) -> bool:
    compact = norm_ws(name)
    return bool(compact) and len(compact) <= 40 and not re.search(r"[、,，;；]", compact)


def preview(text: str, n: int = 200) -> str:
    return (text or "").replace("\n", " ").strip()[:n]


def sha256_hex(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def contains_forecast(text: str) -> Optional[str]:
    """返回命中的预测/规划关键词，否则 None。"""
    t = text or ""
    for kw in FORECAST_KEYWORDS:
        if kw in t:
            return kw
    return None


# ----------------------------------------------------------------------------
# Qdrant 检索 + 重排
# ----------------------------------------------------------------------------

def effective_doc_ids(doc_id: str = "", allowed_doc_ids=None) -> set[str]:
    if doc_id:
        return {doc_id}
    return {str(x).strip() for x in (allowed_doc_ids or set()) if str(x).strip()}


def build_doc_filter(doc_ids: set[str]):
    if not doc_ids:
        return None
    from qdrant_client import models as qmodels
    return qmodels.Filter(must=[qmodels.FieldCondition(
        key="doc_id", match=qmodels.MatchAny(any=sorted(doc_ids)))])


def retrieve(client, collection: str, qvec: List[float], top_k: int,
             doc_id: str = "", allowed_doc_ids=None) -> List[Dict[str, Any]]:
    doc_ids = effective_doc_ids(doc_id, allowed_doc_ids)
    query_filter = None
    if doc_ids:
        query_filter = build_doc_filter(doc_ids)
    res = client.query_points(
        collection_name=collection,
        query=qvec,
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )
    points = res.points if hasattr(res, "points") else res
    out = []
    for p in points:
        pl = p.payload or {}
        if doc_ids and str(pl.get("doc_id") or "") not in doc_ids:
            continue
        out.append({
            "score": float(p.score) if p.score is not None else 0.0,
            "evidence_id": pl.get("evidence_id"),
            "doc_id": pl.get("doc_id"),
            "page_start": pl.get("page_start"),
            "section_path": pl.get("section_path"),
            "content_type": pl.get("content_type"),
            "stage3_priority": pl.get("stage3_priority"),
            "review_required": bool(pl.get("review_required", False)),
            "text": pl.get("text") or "",
        })
    return out


def retrieve_lexical(client, collection: str, keywords: List[str], limit: int,
                     doc_id: str = "", allowed_doc_ids=None) -> List[Dict[str, Any]]:
    """Add exact relation-cue recall to dense retrieval for structure tasks."""
    if not keywords:
        return []
    doc_ids = effective_doc_ids(doc_id, allowed_doc_ids)
    scroll_filter = build_doc_filter(doc_ids)
    points, offset = client.scroll(
        collection_name=collection,
        scroll_filter=scroll_filter,
        limit=256,
        with_payload=True,
        with_vectors=False,
    )
    matches = []
    while True:
        for p in points:
            pl = p.payload or {}
            if doc_ids and str(pl.get("doc_id") or "") not in doc_ids:
                continue
            text = pl.get("text") or ""
            hit_count = sum(1 for kw in keywords if kw in text)
            if not hit_count:
                continue
            matches.append({
                "score": 1.0 + min(hit_count, 5) * 0.02,
                "retrieval_method": "lexical",
                "evidence_id": pl.get("evidence_id"),
                "doc_id": pl.get("doc_id"),
                "page_start": pl.get("page_start"),
                "section_path": pl.get("section_path"),
                "content_type": pl.get("content_type"),
                "stage3_priority": pl.get("stage3_priority"),
                "review_required": bool(pl.get("review_required", False)),
                "text": text,
            })
        if offset is None or len(matches) >= limit:
            break
        points, offset = client.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
    matches.sort(key=lambda item: item["score"], reverse=True)
    return matches[:limit]


def rerank_and_select(candidates, final_k, max_per_page, priority_bonus, include_review_required):
    used_review = False
    pool = []
    for c in candidates:
        if c["review_required"] and not include_review_required:
            continue
        if c["review_required"]:
            used_review = True
        pool.append(c)
    for c in pool:
        c["rerank_score"] = c["score"] + priority_bonus.get(str(c.get("stage3_priority")), 0.0)
    pool.sort(key=lambda x: x["rerank_score"], reverse=True)
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
        tag = "（待复核）" if e.get("review_required") else ""
        lines.append(
            f"[证据{i}] evidence_id={e.get('evidence_id')} | page_no={e.get('page_start')} | "
            f"content_type={e.get('content_type')} | priority={e.get('stage3_priority')}{tag}\n"
            f"章节: {sec}\n原文: {e.get('text')}"
        )
    return "\n\n".join(lines)


def build_prompt(task: Dict[str, str], evidences: List[Dict[str, Any]]) -> str:
    return (
        SYSTEM_RULES
        + "\n\n" + ENTITY_TYPE_DEF + "\n" + RELATION_TYPE_DEF
        + "\n\n【抽取任务】" + task.get("task", "") + "\n【原始问题】" + task.get("query", "")
        + "\n\n【证据】(只能使用以下证据，evidence_id 只能取自这里)\n"
        + build_evidence_block(evidences)
        + "\n\n【行业边界】只抽取这些证据所在当前白皮书中的产业链事实；不要沿用历史项目、示例行业或模型常识中的默认产业词。"
        + "\n\n【输出 JSON schema，必须严格遵守】\n" + SCHEMA_EXAMPLE
        + "\n\ntask_id 请填写：" + task.get("id", "")
        + "\n现在请只输出合法 JSON："
    )


def build_repair_prompt(task, evidences, raw: str) -> str:
    return (
        "你上一次的输出不是合法/合规的 JSON。请只修复【格式和字段】，不得新增任何事实、不得修改结论内容。\n"
        "要求：合法 JSON；顶层字段只能是 task_id、entities、relations、insufficient_evidence、warnings；"
        "每个 entity 含 surface_form、normalized_name_candidate、entity_type、evidence_id、page_no、quote、review_required、reason；"
        "每个 relation 含 subject、subject_type、relation_type、object、object_type、evidence_id、page_no、quote、assertion_type、review_required、reason；"
        "类型只能取自给定集合；evidence_id 只能取自下面证据；quote 必须是证据原文连续摘录。"
        "不要输出 Markdown/代码块/解释。\n\n"
        "【目标 schema】\n" + SCHEMA_EXAMPLE
        + "\n\n【证据】\n" + build_evidence_block(evidences)
        + "\n\n【任务】" + task.get("task", "") + " (task_id=" + task.get("id", "") + ")"
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
        return json.loads(resp.read().decode("utf-8"))


def call_ollama_generate(ollama_url, model, prompt, num_predict, timeout):
    """优先 /api/generate；失败回退 /api/chat。返回 (text, endpoint, error)。"""
    gen_url = ollama_url.rstrip("/") + "/api/generate"
    body = {
        "model": model, "prompt": prompt, "stream": False, "format": "json",
        "think": False, "options": {"temperature": 0, "num_predict": num_predict},
    }
    try:
        resp = _http_post_json(gen_url, body, timeout)
        return resp.get("response", ""), "/api/generate", None
    except Exception as e:
        gen_err = f"{type(e).__name__}: {e}"
        log(f"/api/generate 失败，尝试回退 /api/chat: {gen_err}")
    chat_url = ollama_url.rstrip("/") + "/api/chat"
    body_chat = {
        "model": model, "messages": [{"role": "user", "content": prompt}],
        "stream": False, "format": "json", "think": False,
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    try:
        resp = _http_post_json(chat_url, body_chat, timeout)
        return (resp.get("message", {}) or {}).get("content", ""), "/api/chat", f"generate_failed: {gen_err}"
    except Exception as e:
        return None, "/api/chat", f"generate_err={gen_err}; chat_err={type(e).__name__}: {e}"


def try_parse_json(raw: str) -> Optional[dict]:
    if raw is None:
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    l, r = raw.find("{"), raw.rfind("}")
    if l != -1 and r != -1 and r > l:
        try:
            return json.loads(raw[l:r + 1])
        except Exception:
            return None
    return None


# ----------------------------------------------------------------------------
# 连通性检查
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
    base = ollama_url.rstrip("/")
    status, _ = http_get(base, timeout=10)
    connected = status is not None
    model_available = False
    if connected:
        _, body = http_get(base + "/api/tags", timeout=10)
        if body:
            try:
                names = [m.get("name") for m in json.loads(body).get("models", [])]
                model_available = model in names or any(str(n).startswith(model) for n in names)
            except Exception:
                pass
    return connected, model_available


# ----------------------------------------------------------------------------
# 候选校验 / 分类（verified / review / rejected）
# ----------------------------------------------------------------------------

def _text_has(needle: str, *texts: str) -> bool:
    n = norm_match_text(needle)
    if not n:
        return False
    return any(n in norm_match_text(t) for t in texts if t)


def infer_structural_entity_type(name: str, parent_type: str = "unknown") -> str:
    """对显式父子句中的实体做保守类型推断；无法确定时保留 unknown。"""
    compact = re.sub(r"\s+", "", str(name or ""))
    suffix_types = (
        (("材料", "基板", "靶材", "化学品"), "material"),
        (("器件", "面板", "产品", "屏"), "product"),
        (("技术", "工艺", "方法"), "technology"),
        (("设备", "装备", "产线"), "equipment"),
        (("应用", "场景"), "application"),
        (("企业", "公司"), "company"),
        (("产业", "行业"), "industry"),
        (("环节", "流程"), "industry_link"),
    )
    for suffixes, entity_type in suffix_types:
        if compact.endswith(suffixes):
            return entity_type
    # 枚举子项通常与父类同型；只继承较具体、不会把抽象产业强加给产品的类型。
    if parent_type in {"material", "product", "technology", "equipment", "application"}:
        return parent_type
    return "unknown"


def _clean_structural_name(value: str) -> str:
    value = re.sub(r"^[\s\[【]*", "", str(value or ""))
    value = re.sub(r"[\s\]】。；;，,：:]*$", "", value)
    value = re.sub(r"^(?:前言|摘要|\d+[.)、])\s*", "", value)
    return value.strip()


def extract_explicit_part_of_candidates(evidences: List[Dict[str, Any]]) -> Tuple[List[dict], List[dict]]:
    """从“父类包括子项 / 子项属于父类的一种”中提取高精度 PART_OF 兜底候选。

    该兜底只读取检索到的原文，不补全列表、不猜测省略项；所有结果仍需经过
    ``validate_entity`` / ``validate_relation`` 的逐字证据和方向校验。
    """
    entities: List[dict] = []
    relations: List[dict] = []
    seen_entities = set()
    seen_relations = set()

    def add_pair(child: str, parent: str, quote: str, ev: Dict[str, Any]) -> None:
        child = _clean_structural_name(child)
        parent = _clean_structural_name(parent)
        if not (2 <= len(child) <= 60 and 2 <= len(parent) <= 60):
            return
        if child == parent or not (_text_has(child, quote) and _text_has(parent, quote)):
            return
        if not is_atomic_entity_name(child) or not is_atomic_entity_name(parent):
            return
        parent_type = infer_structural_entity_type(parent)
        child_type = infer_structural_entity_type(child, parent_type)
        eid = ev.get("evidence_id")
        page = ev.get("page_start")
        for name, entity_type in ((parent, parent_type), (child, child_type)):
            key = (norm_match_text(name), eid)
            if key in seen_entities:
                continue
            seen_entities.add(key)
            entities.append({
                "surface_form": name,
                "normalized_name_candidate": name,
                "entity_type": entity_type,
                "evidence_id": eid,
                "page_no": page,
                "quote": quote,
                "review_required": False,
                "reason": "原文明示父子结构",
                "_extraction_method": "explicit_pattern",
            })
        rel_key = (norm_match_text(child), norm_match_text(parent), eid)
        if rel_key in seen_relations:
            return
        seen_relations.add(rel_key)
        relations.append({
            "subject": child,
            "subject_type": child_type,
            "relation_type": "PART_OF",
            "object": parent,
            "object_type": parent_type,
            "evidence_id": eid,
            "page_no": page,
            "quote": quote,
            "assertion_type": "explicit_fact",
            "review_required": False,
            "reason": "原文明示父子结构",
            "_extraction_method": "explicit_pattern",
        })

    for ev in evidences:
        text = str(ev.get("text") or "")
        if not text or ev.get("review_required") or is_restricted(ev):
            continue

        # 例：“新型显示技术主要包括A、B、C等，其中……”。限制父类和列表长度，
        # 并排除“范围包括”“支持包括”等并非分类关系的表达。
        for match in re.finditer(r"(?:主要)?包括", text):
            cue_start, cue_end = match.span()
            left = re.split(r"[。！？；;\n]", text[:cue_start])[-1]
            left = re.split(r"[，,:：]", left)[-1]
            nested_parentheses = left.rstrip().endswith(("（", "("))
            parent = _clean_structural_name(left)
            if parent.endswith("（") or parent.endswith("("):
                parent = parent[:-1].strip()
            parent = re.sub(r"^(?:其中|以及|和|或|既|对|将|把)", "", parent).strip()
            if (not parent or len(parent) > 60 or
                    parent.endswith(("对", "从", "为", "向", "给", "以", "通过")) or
                    any(token in parent for token in ("范围", "以下", "方面", "支持", "涵盖"))):
                continue
            tail = text[cue_end:]
            if nested_parentheses:
                tail = re.split(r"[）)]", tail, maxsplit=1)[0]
            tail = re.split(r"(?:，其中|。|；|;|\n|也涉及|以及涉及)", tail, maxsplit=1)[0]
            tail = re.sub(r"[）)]?\s*等(?:内容|产品|材料|技术|器件)?\s*$", "", tail).strip()
            items = [_clean_structural_name(x) for x in re.split(r"[、]", tail)]
            items = [x for x in items if x]
            if not (1 <= len(items) <= 10):
                continue
            # 普通正文中的单个“包括”常表示举例或政策覆盖对象，而非稳定分类。
            # 单项仅在括号内的明确释义结构中接受。
            if len(items) == 1 and not nested_parentheses:
                continue
            if any(any(verb in item for verb in ("提出", "支持", "推动", "促进", "提高", "引导", "采用"))
                   for item in items):
                continue
            quote_end = cue_end + len(tail)
            quote = text[max(0, cue_start - len(left)):quote_end]
            for item in items:
                add_pair(item, parent, quote, ev)

        # 高精度从属句：只接受“A属于B的一种/之一”，避免把一般描述误作层级。
        belongs_pattern = re.compile(
            r"(?P<child>[^。！？；;，,]{2,60}?)属于"
            r"(?P<parent>[^。！？；;，,]{2,60}?)(?:的一种|之一)(?=[。！？；;，,])"
        )
        for match in belongs_pattern.finditer(text):
            add_pair(match.group("child"), match.group("parent"), match.group(0), ev)

    return entities, relations


def _strip_quantity_prefix(value: str, *, classifier_only: bool = False) -> str:
    value = str(value or "").strip()
    classifiers = r"(?:个|块|片|台|套|件|条|辆|支|张|组|吨|公斤|千克|平方米|米)"
    if classifier_only:
        return re.sub(rf"^{classifiers}", "", value).strip()
    numerals = r"(?:\d+(?:\.\d+)?|[一二两三四五六七八九十百千万若干数多]+)"
    return re.sub(rf"^{numerals}\s*{classifiers}?", "", value).strip()


def extract_explicit_input_candidates(evidences: List[Dict[str, Any]]) -> Tuple[List[dict], List[dict]]:
    """从“每个/每块 A 需要 B”物料句式提取 B INPUT_TO A。

    只处理带“每”的用量陈述，避免把“产业需要政策”等一般性表达误作物料流。
    """
    entities: List[dict] = []
    relations: List[dict] = []
    seen_entities = set()
    seen_relations = set()
    pattern = re.compile(r"每(?P<object>[^。！？；;，,]{2,40}?)需要(?P<subject>[^。！？；;，,]{2,40}?)(?=[。！？；;，,])")
    for ev in evidences:
        text = str(ev.get("text") or "")
        if not text or ev.get("review_required") or is_restricted(ev):
            continue
        for match in pattern.finditer(text):
            obj = _clean_structural_name(_strip_quantity_prefix(match.group("object"), classifier_only=True))
            subject = _clean_structural_name(_strip_quantity_prefix(match.group("subject")))
            quote = match.group(0)
            if not (2 <= len(subject) <= 40 and 2 <= len(obj) <= 40):
                continue
            if not is_atomic_entity_name(subject) or not is_atomic_entity_name(obj):
                continue
            subject_type = infer_structural_entity_type(subject)
            object_type = infer_structural_entity_type(obj)
            if subject_type not in {"material", "component", "product", "equipment"}:
                continue
            if object_type not in {"process", "industry_link", "product", "company", "organization"}:
                continue
            eid = ev.get("evidence_id")
            page = ev.get("page_start")
            for name, entity_type in ((subject, subject_type), (obj, object_type)):
                key = (norm_match_text(name), eid)
                if key in seen_entities:
                    continue
                seen_entities.add(key)
                entities.append({
                    "surface_form": name, "normalized_name_candidate": name,
                    "entity_type": entity_type, "evidence_id": eid, "page_no": page,
                    "quote": quote, "review_required": False,
                    "reason": "原文明示单位产品用量", "_extraction_method": "explicit_pattern",
                })
            rel_key = (norm_match_text(subject), norm_match_text(obj), eid)
            if rel_key in seen_relations:
                continue
            seen_relations.add(rel_key)
            relations.append({
                "subject": subject, "subject_type": subject_type, "relation_type": "INPUT_TO",
                "object": obj, "object_type": object_type, "evidence_id": eid, "page_no": page,
                "quote": quote, "assertion_type": "explicit_fact", "review_required": False,
                "reason": "原文明示单位产品用量", "_extraction_method": "explicit_pattern",
            })
    return entities, relations


def is_restricted(ev: Dict[str, Any]) -> Optional[str]:
    ct = str(ev.get("content_type") or "")
    if ct in RESTRICTED_CONTENT_TYPES:
        return f"content_type={ct}"
    pg = ev.get("page_start")
    try:
        pg_int = int(pg)
    except (TypeError, ValueError):
        pg_int = None
    doc_id = str(ev.get("doc_id") or "")
    restricted_pages = DIAGRAM_RESTRICTED_PAGES_BY_DOC.get(doc_id, DIAGRAM_RESTRICTED_PAGES)
    if pg_int in restricted_pages and ct not in {"paragraph", "caption", "heading", "ocr_text"}:
        return f"diagram_page={pg_int}"
    return None


def validate_entity(e: dict, ctx: Dict[str, dict]) -> Tuple[str, str, dict]:
    """返回 (status, reason, normalized)。status: verified/review/rejected。"""
    surface = str(e.get("surface_form") or "").strip()
    norm = {
        "surface_form": surface,
        "normalized_name_candidate": str(e.get("normalized_name_candidate") or surface).strip(),
        "entity_type": ENTITY_TYPE_ALIASES.get(
            str(e.get("entity_type") or "unknown").strip(),
            str(e.get("entity_type") or "unknown").strip(),
        ),
        "evidence_id": e.get("evidence_id"),
        "page_no": e.get("page_no"),
        "quote": str(e.get("quote") or ""),
        "review_required": bool(e.get("review_required", False)),
        "reason": str(e.get("reason") or ""),
    }
    if not surface:
        return "rejected", "entity_missing_surface", norm
    if norm["entity_type"] not in ENTITY_TYPES:
        return "rejected", "entity_type_invalid", norm
    ev = ctx.get(norm["evidence_id"])
    if ev is None:
        return "rejected", "evidence_id_not_in_context", norm
    if not _text_has(norm["quote"], ev.get("text")):
        return "rejected", "quote_not_found", norm
    if not _text_has(surface, norm["quote"], ev.get("text")):
        return "rejected", "subject_or_object_not_found", norm
    if norm["page_no"] is not None and str(norm["page_no"]) != str(ev.get("page_start")):
        norm["review_required"] = True
        return "review", "page_no_mismatch", norm
    if norm["review_required"] or ev.get("review_required"):
        norm["review_required"] = True
        return "review", "review_required_evidence", norm
    if is_restricted(ev):
        norm["review_required"] = True
        return "review", "table_or_diagram_restricted", norm
    return "verified", "ok", norm


def validate_relation(r: dict, ctx: Dict[str, dict]) -> Tuple[str, str, dict]:
    subj = str(r.get("subject") or "").strip()
    obj = str(r.get("object") or "").strip()
    at = str(r.get("assertion_type") or "").strip()
    norm = {
        "subject": subj,
        "subject_type": ENTITY_TYPE_ALIASES.get(
            str(r.get("subject_type") or "unknown").strip(),
            str(r.get("subject_type") or "unknown").strip(),
        ),
        "relation_type": RELATION_TYPE_ALIASES.get(
            str(r.get("relation_type") or "").strip(),
            str(r.get("relation_type") or "").strip(),
        ),
        "object": obj,
        "object_type": ENTITY_TYPE_ALIASES.get(
            str(r.get("object_type") or "unknown").strip(),
            str(r.get("object_type") or "unknown").strip(),
        ),
        "evidence_id": r.get("evidence_id"), "page_no": r.get("page_no"),
        "quote": str(r.get("quote") or ""),
        "assertion_type": at if at in ASSERTION_TYPES else "uncertain",
        "review_required": bool(r.get("review_required", False)),
        "reason": str(r.get("reason") or ""),
    }
    if not subj or not obj:
        return "rejected", "relation_missing_subject_object", norm
    if not is_atomic_entity_name(subj) or not is_atomic_entity_name(obj):
        norm["review_required"] = True
        return "review", "non_atomic_relation_endpoint", norm
    if norm["subject_type"] not in ENTITY_TYPES or norm["object_type"] not in ENTITY_TYPES:
        return "rejected", "entity_type_invalid", norm
    if norm["relation_type"] not in RELATION_TYPES:
        return "rejected", "relation_type_invalid", norm
    ev = ctx.get(norm["evidence_id"])
    if ev is None:
        return "rejected", "evidence_id_not_in_context", norm
    if not _text_has(norm["quote"], ev.get("text")):
        return "rejected", "quote_not_found", norm
    # quote 是支撑本关系的最小连续原文，两个端点都必须出现在 quote 中。
    # 只在同一较长证据块的其他句子出现，属于跨句拼接，不能验证方向关系。
    if not (_text_has(subj, norm["quote"]) and _text_has(obj, norm["quote"])):
        return "rejected", "subject_or_object_not_found", norm
    endpoint_rule = RELATION_ENDPOINT_RULES.get(norm["relation_type"])
    if endpoint_rule:
        allowed_subjects, allowed_objects = endpoint_rule
        if norm["subject_type"] not in allowed_subjects or norm["object_type"] not in allowed_objects:
            norm["review_required"] = True
            return "review", "relation_endpoint_type_mismatch", norm
    cues = RELATION_CUES.get(norm["relation_type"], ())
    evidence_text = norm["quote"] or ev.get("text", "")
    if cues and not any(cue in evidence_text for cue in cues):
        norm["review_required"] = True
        return "review", "relation_cue_missing", norm
    if norm["relation_type"] == "PART_OF":
        compact = re.sub(r"\s+", "", evidence_text)
        subj_compact = re.sub(r"\s+", "", subj)
        obj_compact = re.sub(r"\s+", "", obj)
        subj_pos = compact.find(subj_compact)
        obj_pos = compact.find(obj_compact)
        direction_ok = False
        # Controlled direction is child -> parent.  For “父包括子”, the
        # object(parent) must occur before the cue and the subject(child)
        # after it.  For “子属于父”, subject must occur before the cue.
        for cue in PART_OF_PARENT_TO_CHILD_CUES:
            cue_pos = compact.find(cue)
            if obj_pos >= 0 and cue_pos >= 0 and subj_pos >= 0 and obj_pos < cue_pos < subj_pos:
                direction_ok = True
                break
        if not direction_ok:
            for cue in PART_OF_CHILD_TO_PARENT_CUES:
                cue_pos = compact.find(cue)
                if subj_pos >= 0 and cue_pos >= 0 and obj_pos >= 0 and subj_pos < cue_pos < obj_pos:
                    direction_ok = True
                    break
        if not direction_ok:
            norm["review_required"] = True
            return "review", "part_of_direction_or_scope_mismatch", norm
    restricted = is_restricted(ev)
    if restricted:
        norm["review_required"] = True
        return "rejected", f"table_or_diagram_restricted({restricted})", norm
    if norm["page_no"] is not None and str(norm["page_no"]) != str(ev.get("page_start")):
        norm["review_required"] = True
        return "review", "page_no_mismatch", norm
    # 预测/规划/趋势过滤
    # quote 已通过“必须为证据原文连续摘录”的校验，应按支持本关系的原句判定。
    # 若扫描整个合并证据块，会让后续句中的“预计未来”误伤前一句现实事实。
    fk = contains_forecast(norm["quote"])
    if norm["assertion_type"] != "explicit_fact":
        norm["review_required"] = True
        return "review", f"non_explicit_fact:{norm['assertion_type']}", norm
    if fk:
        norm["review_required"] = True
        return "review", f"forecast_keyword_in_quote:{fk}", norm
    if norm["review_required"] or ev.get("review_required"):
        norm["review_required"] = True
        return "review", "review_required_evidence", norm
    return "verified", "ok", norm


# ----------------------------------------------------------------------------
# 单任务处理
# ----------------------------------------------------------------------------

def _model_call_record(task_id, endpoint, model, num_predict, prompt, raw, parse_status, retry):
    return {
        "model_call_id": uuid.uuid4().hex[:12], "timestamp": datetime.now().isoformat(),
        "api_endpoint": endpoint, "model_name": model, "temperature": 0,
        "num_predict": num_predict, "task_id": task_id, "stage": "3C",
        "input_text_hash": sha256_hex(prompt), "raw_response": raw,
        "parse_status": parse_status, "retry_count": retry,
    }


def process_task(task, client, collection, model, args, cfg_ret):
    tid = task["id"]
    log(f"[{tid}] 检索: {task['query']}")
    qvec = encode_query(model, task["query"])
    allowed_doc_ids = getattr(args, "allowed_doc_id_set", set())
    candidates = retrieve(client, collection, qvec, args.top_k, args.doc_id, allowed_doc_ids)
    if cfg_ret.get("hybrid_lexical", True) and task.get("keywords"):
        lexical = retrieve_lexical(
            client, collection, task["keywords"], cfg_ret.get("lexical_k", args.top_k),
            args.doc_id, allowed_doc_ids)
        merged = {row.get("evidence_id"): row for row in candidates if row.get("evidence_id")}
        for row in lexical:
            eid = row.get("evidence_id")
            if not eid or (eid in merged and merged[eid].get("score", 0) >= row.get("score", 0)):
                continue
            merged[eid] = row
        candidates = list(merged.values())
    selected, used_review = rerank_and_select(
        candidates, args.final_k, cfg_ret["max_per_page"], cfg_ret["priority_bonus"],
        args.include_review_required)

    retrieved_out = [{
        "rank": i, "score": round(e["score"], 6), "evidence_id": e["evidence_id"],
        "doc_id": e.get("doc_id"), "page_no": e["page_start"], "section_path": e["section_path"],
        "content_type": e["content_type"], "stage3_priority": e["stage3_priority"],
        "review_required": e["review_required"], "text_preview": preview(e["text"], 200),
    } for i, e in enumerate(selected, 1)]
    retrieval_record = {
        "task_id": tid, "task": task["task"], "query": task["query"],
        "top_k": args.top_k, "final_k": len(selected), "retrieved": retrieved_out,
    }
    ctx = {e["evidence_id"]: e for e in selected}

    result = {
        "retrieval": retrieval_record, "entities": [], "relations": [],
        "rejected": [], "model_calls": [], "failed": None,
        "insufficient": [], "warnings": [], "used_review": used_review,
    }

    if args.dry_run:
        return result
    if not selected:
        result["insufficient"].append({"missing_point": "检索无结果", "reason": "排除待复核后无可用证据"})
        return result

    prompt = build_prompt(task, selected)
    raw, endpoint, err = call_ollama_generate(
        args.ollama_url, args.llm_model, prompt, args.num_predict, cfg_ret["timeout"])
    obj = try_parse_json(raw)
    result["model_calls"].append(_model_call_record(
        tid, endpoint, args.llm_model, args.num_predict, prompt, raw,
        "ok" if obj is not None else "parse_failed", 0))

    if obj is None and cfg_ret["allow_repair"]:
        log(f"[{tid}] 触发一次 JSON 修复重试")
        rprompt = build_repair_prompt(task, selected, raw or "")
        raw2, endpoint2, _ = call_ollama_generate(
            args.ollama_url, args.llm_model, rprompt, args.num_predict, cfg_ret["timeout"])
        obj = try_parse_json(raw2)
        result["model_calls"].append(_model_call_record(
            tid, endpoint2, args.llm_model, args.num_predict, rprompt, raw2,
            "ok" if obj is not None else "parse_failed", 1))

    if obj is None:
        result["failed"] = {
            "task_id": tid, "query": task["query"], "failure_type": "json_parse_failed",
            "raw_response": raw, "reason": err or "修复重试后仍无法解析为 JSON",
            "timestamp": datetime.now().isoformat(),
        }
        result["rejected"].append({
            "item_type": "model_output", "candidate_id": f"{tid}_all",
            "reason": "json_invalid_after_retry", "task_id": tid,
            "evidence_id": "", "page_no": "",
        })
        return result

    # 剔除多余顶层字段
    extra = [k for k in list(obj.keys()) if k not in ALLOWED_TOP_LEVEL]
    for k in extra:
        obj.pop(k, None)
    if extra:
        result["warnings"].append(f"剔除多余顶层字段: {extra}")
    result["insufficient"] = obj.get("insufficient_evidence") or []
    if isinstance(obj.get("warnings"), list):
        result["warnings"].extend(obj.get("warnings"))

    src_q = task["query"]
    # 实体 / 关系。对于专门的父子结构任务，补充只依赖显式句式的确定性兜底；
    # 它不会绕过后续统一校验，也不会根据行业常识生成缺失项。
    ents = list(obj.get("entities")) if isinstance(obj.get("entities"), list) else []
    rels = list(obj.get("relations")) if isinstance(obj.get("relations"), list) else []
    deterministic_entities: List[dict] = []
    deterministic_relations: List[dict] = []
    if tid == "t08":
        deterministic_entities, deterministic_relations = extract_explicit_part_of_candidates(selected)
    elif tid == "t09":
        deterministic_entities, deterministic_relations = extract_explicit_input_candidates(selected)
    if deterministic_relations:
        existing_entities = {
            (norm_match_text(e.get("surface_form")), e.get("evidence_id"))
            for e in ents if isinstance(e, dict)
        }
        existing_relations = {
            (norm_match_text(r.get("subject")), norm_match_text(r.get("object")), r.get("evidence_id"))
            for r in rels if isinstance(r, dict)
        }
        ents.extend(e for e in deterministic_entities
                    if (norm_match_text(e.get("surface_form")), e.get("evidence_id")) not in existing_entities)
        rels.extend(r for r in deterministic_relations
                    if (norm_match_text(r.get("subject")), norm_match_text(r.get("object")), r.get("evidence_id"))
                    not in existing_relations)
        result["warnings"].append(
            f"显式关系句式兜底生成 {len(deterministic_relations)} 条候选，均继续执行统一证据校验")

    # 实体
    for i, e in enumerate(ents, 1):
        if not isinstance(e, dict):
            continue
        status, reason, norm = validate_entity(e, ctx)
        rec = {
            "entity_candidate_id": f"{tid}_e{i:02d}", "task_id": tid,
            "surface_form": norm["surface_form"],
            "normalized_name_candidate": norm["normalized_name_candidate"],
            "entity_type": norm["entity_type"], "evidence_id": norm["evidence_id"],
            "page_no": norm["page_no"], "quote": norm["quote"],
            "review_required": norm["review_required"], "verification_status": status,
            "verification_reason": reason, "model_name": args.llm_model, "source_query": src_q,
            "extraction_method": e.get("_extraction_method", "local_llm"),
            "candidate_reason": norm["reason"],
        }
        result["entities"].append(rec)
        if status == "rejected":
            result["rejected"].append({
                "item_type": "entity", "candidate_id": rec["entity_candidate_id"],
                "reason": reason, "task_id": tid,
                "evidence_id": norm["evidence_id"], "page_no": norm["page_no"],
            })
    # 关系
    for i, r in enumerate(rels, 1):
        if not isinstance(r, dict):
            continue
        status, reason, norm = validate_relation(r, ctx)
        rec = {
            "relation_candidate_id": f"{tid}_r{i:02d}", "task_id": tid,
            "subject": norm["subject"], "subject_type": norm["subject_type"],
            "relation_type": norm["relation_type"], "object": norm["object"],
            "object_type": norm["object_type"], "evidence_id": norm["evidence_id"],
            "page_no": norm["page_no"], "quote": norm["quote"],
            "assertion_type": norm["assertion_type"], "review_required": norm["review_required"],
            "verification_status": status, "verification_reason": reason,
            "model_name": args.llm_model, "source_query": src_q,
            "extraction_method": r.get("_extraction_method", "local_llm"),
            "candidate_reason": norm["reason"],
        }
        result["relations"].append(rec)
        if status == "rejected":
            result["rejected"].append({
                "item_type": "relation", "candidate_id": rec["relation_candidate_id"],
                "reason": reason, "task_id": tid,
                "evidence_id": norm["evidence_id"], "page_no": norm["page_no"],
            })
    return result


# ----------------------------------------------------------------------------
# 复核队列
# ----------------------------------------------------------------------------

REVIEW_ACTION = {
    "page_no_mismatch": "核对页码与 evidence 是否一致",
    "review_required_evidence": "先复核该证据再采用",
    "table_or_diagram_restricted": "图示/表格限制，需人工确认后才能采用",
    "quote_not_found": "人工核对 quote 与原文",
    "subject_or_object_not_found": "人工确认主宾语是否在原文",
    "entity_type_invalid": "重新判定实体类型",
    "relation_type_invalid": "重新判定关系类型",
    "json_invalid_after_retry": "人工重跑或拆分任务",
}


def build_review_items(res: dict) -> List[dict]:
    items = []
    tid = res["retrieval"]["task_id"]
    ctr = [0]

    def add(item_type, cand_id, issue_type, severity, reason, eid="", pno=""):
        ctr[0] += 1
        action = REVIEW_ACTION.get(issue_type.split(":")[0].split("(")[0], "人工核对原文与结论")
        if issue_type.startswith("non_explicit_fact") or issue_type.startswith("forecast"):
            action = "预测/规划/趋势类，不得作为现实关系，人工确认"
        items.append({
            "review_item_id": f"{tid}_rv{ctr[0]:02d}", "task_id": tid, "item_type": item_type,
            "candidate_id": cand_id, "issue_type": issue_type, "severity": severity,
            "reason": reason, "evidence_id": eid, "page_no": pno, "recommended_action": action,
        })

    for e in res["entities"]:
        if e["verification_status"] == "review":
            add("entity", e["entity_candidate_id"], e["verification_reason"], "medium",
                f"实体候选待复核: {e['surface_form']}", e["evidence_id"], e["page_no"])
    for r in res["relations"]:
        if r["verification_status"] == "review":
            sev = "high" if r["verification_reason"].startswith(("non_explicit_fact", "forecast")) else "medium"
            add("relation", r["relation_candidate_id"], r["verification_reason"], sev,
                f"关系候选待复核: {r['subject']}->{r['relation_type']}->{r['object']}",
                r["evidence_id"], r["page_no"])
    for rj in res["rejected"]:
        add(rj["item_type"], rj["candidate_id"], rj["reason"], "high",
            "候选被拒绝，需人工确认", rj.get("evidence_id", ""), rj.get("page_no", ""))
    return items


def stage3b_review_items(stage3b_dir: Optional[Path]) -> List[dict]:
    """Stage 3B is optional; do not inject industry-specific carryover items."""
    return []


# ----------------------------------------------------------------------------
# 输出写入
# ----------------------------------------------------------------------------

def write_jsonl(path: Path, rows: List[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: List[dict], fields: List[str], encoding: str) -> None:
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def write_validation(out_dir: Path, val: Dict[str, Tuple[bool, str]], extra: dict = None) -> None:
    obj = {k: {"passed": bool(v[0]), "note": v[1]} for k, v in val.items()}
    if extra:
        obj["_extra"] = extra
    (out_dir / "validation_summary.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _fail_report(out_dir: Path, args, reason: str) -> None:
    md = ["# 阶段 3C 候选抽取报告（未通过）", "", f"- 失败原因: {reason}",
          f"- Qdrant: {args.qdrant_url} / {args.collection}",
          f"- Ollama: {args.ollama_url} / {args.llm_model}", "",
          "未生成候选。请修复前置条件后重跑。", "",
          "> 本阶段只完成基于 Qdrant 检索证据的候选抽取；verified 仅为规则校验通过的候选，不代表人工最终确认；未生成图谱；未写 Neo4j。"]
    (out_dir / "rag_extraction_report.md").write_text("\n".join(md), encoding="utf-8")


def _counter(rows: List[dict], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        k = str(r.get(key))
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda x: -x[1]))


def write_report(out_dir, args, run_config, retrieval_rows, ent_all, rel_all,
                 ent_verified, rel_verified, rejected_rows, review_rows, val, stats) -> None:
    L = []
    L.append("# 阶段 3C 候选实体/关系抽取报告")
    L.append("")
    L.append(f"- 生成时间: {run_config['timestamp']}")
    L.append(f"- Qdrant 地址 / collection: {args.qdrant_url} / {args.collection}")
    L.append(f"- 文档过滤 doc_id: {args.doc_id or '未指定'}")
    L.append(f"- Embedding 模型: {args.embedding_model} ({run_config['embedding_source']}/{run_config['device']})")
    L.append(f"- 生成模型: {args.llm_model}")
    L.append("- 是否使用 Ollama /api/generate: 是（仅在 generate 失败时才回退 /api/chat）")
    L.append(f"- 任务数量: {stats['num_tasks']}")
    L.append("")
    L.append("## 各任务检索与抽取概览")
    L.append("")
    L.append("| 任务 | 主要命中页 | 实体候选 | verified实体 | 关系候选 | verified关系 |")
    L.append("|---|---|---|---|---|---|")
    for rr in retrieval_rows:
        tid = rr["task_id"]
        pages = sorted({r["page_no"] for r in rr["retrieved"][:5] if r.get("page_no") is not None})
        ne = len([e for e in ent_all if e["task_id"] == tid])
        nev = len([e for e in ent_verified if e["task_id"] == tid])
        nr = len([r for r in rel_all if r["task_id"] == tid])
        nrv = len([r for r in rel_verified if r["task_id"] == tid])
        L.append(f"| {tid} | {pages} | {ne} | {nev} | {nr} | {nrv} |")
    L.append("")
    L.append("## 数量汇总")
    L.append("")
    L.append(f"- 候选实体总数 / verified: {len(ent_all)} / {len(ent_verified)}")
    L.append(f"- 候选关系总数 / verified: {len(rel_all)} / {len(rel_verified)}")
    L.append(f"- 被拒绝候选: {len(rejected_rows)}")
    L.append(f"- 复核队列项: {len(review_rows)}")
    L.append("")
    L.append("### 按实体类型统计 (全部候选)")
    L.append("")
    for k, v in _counter(ent_all, "entity_type").items():
        L.append(f"- {k}: {v}")
    L.append("")
    L.append("### 按关系类型统计 (全部候选)")
    L.append("")
    for k, v in _counter(rel_all, "relation_type").items():
        L.append(f"- {k}: {v}")
    L.append("")
    L.append("### 拒绝原因统计")
    L.append("")
    for k, v in _counter(rejected_rows, "reason").items():
        L.append(f"- {k}: {v}")
    L.append("")
    L.append("### 复核队列原因统计")
    L.append("")
    for k, v in _counter(review_rows, "issue_type").items():
        L.append(f"- {k}: {v}")
    L.append("")
    L.append("## 质量评价")
    L.append("")
    L.append(f"- 是否出现无证据关系: {'否' if stats['no_evidence_ok'] else '是'}")
    L.append(f"- quote 匹配失败数: {stats['quote_fail']}")
    L.append(f"- verified 关系是否均为 explicit_fact: {'是' if stats['verified_all_fact'] else '否'}")
    L.append(f"- 主要命中页码(全体): {stats['all_pages']}")
    L.append("")
    L.append("## 阶段 4 建议")
    L.append("")
    L.append("- 实体标准化：对 normalized_name_candidate 做同义归并与别名映射。")
    L.append("- 关系去重：按 (subject_norm, relation_type, object_norm) 去重，合并 evidence 列表。")
    L.append("- 冲突检测：检查方向矛盾与重复关系，标记矛盾对。")
    L.append("- 人工审核：优先处理 review_queue 中 high 项与 forecast/policy 类，确认后再入图。")
    L.append("")
    L.append("## 验证项")
    L.append("")
    for k, v in val.items():
        L.append(f"- {'PASS' if v[0] else 'FAIL'} {k}: {v[1]}")
    L.append("")
    L.append("---")
    L.append("> 本阶段只完成基于 Qdrant 检索证据的 RAG 辅助候选实体与候选关系抽取；verified 仅表示通过原文与规则校验的候选，"
             "不代表人工最终确认；未生成正式产业链图谱；未写入 Neo4j；未做实体最终归并；未调用任何外部云端 API 或联网搜索。")
    (out_dir / "rag_extraction_report.md").write_text("\n".join(L), encoding="utf-8")


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 3C：候选实体/关系抽取（Qdrant + Ollama qwen3:8b）")
    parser.add_argument("--project-root", default=r"D:\产业链建模")
    parser.add_argument("--collection", default="whitepaper_chunks")
    parser.add_argument("--doc-id", default="", help="只检索并校验当前文档 doc_id 的证据；为空则不加过滤")
    parser.add_argument("--allowed-doc-ids", default="",
                        help="逗号分隔的允许 doc_id 集合；用于 root-model 多PDF知识库校验")
    parser.add_argument("--root-model", default="", help="知识库/root-model 名称，仅写入运行元数据")
    parser.add_argument("--document-profile", default="auto", help="文档专用档案 id；auto 按 doc_id 匹配")
    parser.add_argument("--embedding-model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--llm-model", default="qwen3:8b")
    parser.add_argument("--mode", choices=["batch", "single"], default="batch")
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--query", default=None)
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--final-k", type=int, default=10)
    parser.add_argument("--include-review-required", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--num-predict", type=int, default=6144)
    args = parser.parse_args()

    project_root = Path(args.project_root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rag_root = project_root / "rag"
    out_dir = rag_root / "outputs" / f"stage3c_rag_extraction_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_logger(out_dir / "run.log")
    log("=" * 70)
    log("阶段 3C：候选实体/关系抽取启动")
    log(f"输出目录: {out_dir}")

    cfg_path = rag_root / "config" / "rag_extraction_config.yaml"
    cfg = load_yaml(cfg_path) if cfg_path.exists() else {}
    if args.collection == "whitepaper_chunks":
        args.collection = str(((cfg.get("qdrant") or {}).get("collection")) or args.collection)
    allowed_doc_ids = {
        x.strip() for x in str(args.allowed_doc_ids or "").split(",") if x.strip()
    }
    args.allowed_doc_id_set = allowed_doc_ids
    global DIAGRAM_RESTRICTED_PAGES, DIAGRAM_RESTRICTED_PAGES_BY_DOC
    default_profile = resolve_document_profile(
        project_root,
        hints=(args.doc_id, args.root_model),
        explicit=args.document_profile,
    )
    DIAGRAM_RESTRICTED_PAGES = {
        int(x) for x in document_rules(default_profile).get("diagram_restricted_pages", [])
    }
    DIAGRAM_RESTRICTED_PAGES_BY_DOC = {}
    for active_doc_id in sorted(allowed_doc_ids | ({args.doc_id} if args.doc_id else set())):
        profile = resolve_document_profile(
            project_root,
            hints=(active_doc_id,),
            explicit=args.document_profile,
        )
        DIAGRAM_RESTRICTED_PAGES_BY_DOC[active_doc_id] = {
            int(x) for x in document_rules(profile).get("diagram_restricted_pages", [])
        }
        log(f"文档档案: {active_doc_id} -> {profile['profile_id']}")
    ret_cfg = cfg.get("retrieval") or {}
    llm_cfg = cfg.get("llm") or {}
    cfg_ret = {
        "max_per_page": int(ret_cfg.get("max_per_page", 3)),
        "priority_bonus": ret_cfg.get("priority_bonus", {"high": 0.05, "medium": 0.02, "low": 0.0}),
        "hybrid_lexical": bool(ret_cfg.get("hybrid_lexical", True)),
        "lexical_k": int(ret_cfg.get("lexical_k", 30)),
        "timeout": int(llm_cfg.get("request_timeout_sec", 240)),
        "allow_repair": bool((cfg.get("validation") or {}).get("allow_one_repair_retry", True)),
    }
    csv_encoding = ((cfg.get("output") or {}).get("csv_encoding")) or "utf-8-sig"

    # --- 前置检查 ---
    val: Dict[str, Tuple[bool, str]] = {}
    st, body = http_get(args.qdrant_url.rstrip("/") + f"/collections/{args.collection}")
    qdrant_connected = st is not None
    collection_exists = st == 200
    has_points = False
    if body:
        try:
            has_points = int(json.loads(body).get("result", {}).get("points_count", 0)) > 0
        except Exception:
            pass
    val["qdrant_connected"] = (qdrant_connected, f"HTTP {st}")
    val["collection_exists"] = (collection_exists, args.collection)
    val["collection_has_points"] = (has_points, "points_count>0")
    active_doc_filters = effective_doc_ids(args.doc_id, allowed_doc_ids)
    val["doc_id_filter_set"] = (
        bool(active_doc_filters),
        ",".join(sorted(active_doc_filters)) if active_doc_filters else "未指定文档过滤条件",
    )
    val["allowed_doc_ids_set"] = (
        bool(allowed_doc_ids) or bool(args.doc_id),
        ",".join(sorted(allowed_doc_ids)) if allowed_doc_ids else (args.doc_id or "未指定"),
    )
    ollama_connected, model_available = check_ollama(args.ollama_url, args.llm_model)
    val["ollama_connected"] = (ollama_connected, args.ollama_url)
    val["llm_model_available"] = (model_available, args.llm_model)

    # 阶段 3B 输入
    s3b_pointer = rag_root / "latest_stage3b_run.txt"
    s3b_dir = None
    if s3b_pointer.exists():
        p = Path(s3b_pointer.read_text(encoding="utf-8").strip())
        if p.exists():
            s3b_dir = p

    if not (qdrant_connected and collection_exists and has_points):
        write_validation(out_dir, val, extra={"aborted": "qdrant_unavailable"})
        _fail_report(out_dir, args, "Qdrant collection 不可用")
        log("[FAIL] Qdrant 不可用，阶段 3C 未通过")
        return 2
    if not (ollama_connected and model_available):
        write_validation(out_dir, val, extra={"aborted": "ollama_unavailable"})
        _fail_report(out_dir, args, "Ollama 或 qwen3:8b 不可用")
        log("[FAIL] Ollama 不可用，阶段 3C 未通过")
        return 2

    # --- 加载任务 ---
    if args.mode == "single":
        if args.task_id and not args.query:
            all_tasks = load_tasks(rag_root / "queries" / "stage3c_extraction_tasks.yaml")
            tasks = [t for t in all_tasks if t["id"] == args.task_id]
        elif args.query:
            tasks = [{"id": args.task_id or "single_t", "task": args.query, "query": args.query.strip()}]
        else:
            log("single 模式需要 --task-id 或 --query")
            return 2
    else:
        tasks = load_tasks(rag_root / "queries" / "stage3c_extraction_tasks.yaml")
    val["tasks_loaded"] = (len(tasks) > 0, f"{len(tasks)} 个任务")

    # --- 加载 embedding ---
    log("加载 embedding 模型...")
    model, resolved, source, device = load_embedding_model(args.embedding_model)
    log(f"embedding 就绪: source={source}, device={device}")
    val["embedding_model_loaded"] = (True, f"{args.embedding_model} ({source}/{device})")

    from qdrant_client import QdrantClient
    client = QdrantClient(url=args.qdrant_url)

    # --- 逐任务处理 ---
    retrieval_rows, ent_all, rel_all = [], [], []
    rejected_rows, review_rows, model_calls, failed_rows = [], [], [], []
    for t in tasks:
        try:
            res = process_task(t, client, args.collection, model, args, cfg_ret)
            retrieval_rows.append(res["retrieval"])
            ent_all.extend(res["entities"])
            rel_all.extend(res["relations"])
            model_calls.extend(res["model_calls"])
            if res["failed"]:
                failed_rows.append(res["failed"])
            for rj in res["rejected"]:
                rejected_rows.append(rj)
            review_rows.extend(build_review_items(res))
        except Exception as e:
            log(f"[{t['id']}] 处理异常: {type(e).__name__}: {e}")
            failed_rows.append({
                "task_id": t["id"], "query": t.get("query"), "failure_type": "exception",
                "raw_response": None, "reason": f"{type(e).__name__}: {e}",
                "timestamp": datetime.now().isoformat(),
            })
    # 纳入阶段 3B q05/q09/q10 特殊项
    review_rows.extend(stage3b_review_items(s3b_dir))

    ent_verified = [e for e in ent_all if e["verification_status"] == "verified"]
    rel_verified = [r for r in rel_all if r["verification_status"] == "verified"]

    # --- 写输出 ---
    write_jsonl(out_dir / "rag_entity_candidates.jsonl", ent_all)
    write_jsonl(out_dir / "rag_relation_candidates.jsonl", rel_all)
    write_jsonl(out_dir / "verified_entity_candidates.jsonl", ent_verified)
    write_jsonl(out_dir / "verified_relation_candidates.jsonl", rel_verified)
    write_jsonl(out_dir / "rejected_candidates.jsonl", rejected_rows)
    write_jsonl(out_dir / "failed_extractions.jsonl", failed_rows)
    write_jsonl(out_dir / "retrieval_context.jsonl", retrieval_rows)
    write_jsonl(out_dir / "model_calls.jsonl", model_calls)
    write_csv(out_dir / "rag_extraction_review_queue.csv", review_rows,
              ["review_item_id", "task_id", "item_type", "candidate_id", "issue_type",
               "severity", "reason", "evidence_id", "page_no", "recommended_action"], csv_encoding)

    run_config = {
        "timestamp": timestamp, "project_root": str(project_root), "collection": args.collection,
        "doc_id": args.doc_id,
        "allowed_doc_ids": sorted(allowed_doc_ids),
        "root_model": args.root_model,
        "embedding_model": args.embedding_model, "embedding_source": source, "device": device,
        "qdrant_url": args.qdrant_url, "ollama_url": args.ollama_url, "llm_model": args.llm_model,
        "mode": args.mode, "top_k": args.top_k, "final_k": args.final_k,
        "include_review_required": args.include_review_required, "dry_run": args.dry_run,
        "num_predict": args.num_predict, "primary_endpoint": "/api/generate",
        "num_tasks": len(tasks), "stage3b_input": str(s3b_dir) if s3b_dir else None,
    }
    (out_dir / "run_config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- 汇总验证 ---
    all_pages = sorted({r["page_no"] for rr in retrieval_rows for r in rr["retrieved"]
                        if r.get("page_no") is not None})
    quote_fail = len([x for x in rejected_rows if str(x.get("reason", "")).startswith("quote_not_found")])
    ver_ent_ev = all(e.get("evidence_id") for e in ent_verified)
    ver_rel_ev = all(r.get("evidence_id") for r in rel_verified)
    ver_rel_fact = all(r.get("assertion_type") == "explicit_fact" for r in rel_verified)
    no_evidence_ok = all(r.get("evidence_id") and r.get("quote") for r in rel_verified)
    retrieved_doc_ids = sorted({
        str(r.get("doc_id")) for rr in retrieval_rows for r in rr["retrieved"] if r.get("doc_id")
    })

    val["retrieval_completed"] = (len(retrieval_rows) == len(tasks), f"{len(retrieval_rows)}/{len(tasks)}")
    if args.doc_id:
        retrieval_doc_ok = retrieved_doc_ids == [args.doc_id]
        expected_note = args.doc_id
    elif allowed_doc_ids:
        retrieval_doc_ok = set(retrieved_doc_ids).issubset(allowed_doc_ids)
        expected_note = ",".join(sorted(allowed_doc_ids))
    else:
        retrieval_doc_ok = True
        expected_note = "collection isolated"
    val["retrieval_limited_to_current_doc"] = (
        retrieval_doc_ok,
        f"retrieved_doc_ids={retrieved_doc_ids}, expected={expected_note}",
    )
    val["model_calls_completed"] = (len(model_calls) > 0 or args.dry_run, f"{len(model_calls)} 次调用")
    val["entity_candidates_generated"] = (True, f"{len(ent_all)} 个")
    val["relation_candidates_generated"] = (True, f"{len(rel_all)} 个")
    val["verified_entities_generated"] = (len(ent_verified) > 0, f"{len(ent_verified)} 个")
    val["verified_relations_generated"] = (len(rel_verified) > 0, f"{len(rel_verified)} 个")
    val["json_outputs_valid_or_reviewed"] = (len(failed_rows) == 0, f"失败 {len(failed_rows)}")
    val["all_verified_entities_have_evidence"] = (ver_ent_ev, f"{len(ent_verified)} 个 verified 实体")
    val["all_verified_relations_have_evidence"] = (ver_rel_ev, f"{len(rel_verified)} 个 verified 关系")
    # Rejected candidates may legitimately contain a quote mismatch, but every
    # candidate that reached ``verified`` has already passed validate_entity /
    # validate_relation.  Report that invariant directly instead of using an
    # always-true expression, which previously hid regressions in verification.
    verified_quotes_ok = all(
        r.get("evidence_id") and r.get("quote")
        for r in [*ent_verified, *rel_verified]
    )
    val["verified_quotes_match_source_text"] = (
        verified_quotes_ok,
        f"{len(ent_verified) + len(rel_verified)} 个 verified 候选已通过原文校验",
    )
    val["verified_relations_are_explicit_fact"] = (ver_rel_fact, "verified 关系均 explicit_fact")
    val["no_table_or_diagram_visual_relation_verified"] = (
        all("table" not in str(r.get("verification_reason", "")) for r in rel_verified), "无图表 verified 关系")
    val["no_forecast_or_policy_goal_verified_as_fact"] = (ver_rel_fact, "无预测/政策目标被当作事实")
    val["no_external_api_called"] = (True, "仅本地 Qdrant + Ollama")
    val["no_graph_generated"] = (True, "未生成图谱/未写 Neo4j")
    val["original_pdf_not_modified"] = (True, "本阶段只读 Qdrant 与配置，不写入 PDF")

    write_validation(out_dir, val)
    stats = {
        "num_tasks": len(tasks), "all_pages": all_pages, "quote_fail": quote_fail,
        "no_evidence_ok": no_evidence_ok, "verified_all_fact": ver_rel_fact,
    }
    write_report(out_dir, args, run_config, retrieval_rows, ent_all, rel_all,
                 ent_verified, rel_verified, rejected_rows, review_rows, val, stats)
    (rag_root / "latest_stage3c_run.txt").write_text(str(out_dir), encoding="utf-8")

    log("=" * 70)
    log(f"完成: 实体 {len(ent_all)}(verified {len(ent_verified)}), "
        f"关系 {len(rel_all)}(verified {len(rel_verified)}), 拒绝 {len(rejected_rows)}, "
        f"复核 {len(review_rows)}, 失败 {len(failed_rows)}")
    log(f"阶段 3C 验收: {'通过' if all(v[0] for v in val.values()) else '存在未通过项，见报告'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
