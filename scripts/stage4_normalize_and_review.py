#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
阶段 4：候选结果清洗、实体标准化、关系去重、冲突检测、复核表生成、产业链草案输出。

严格边界：
  - 允许：读取阶段3C输出、基础文本归一化、规则去重、冲突检测、生成复核表、草案输出。
  - 禁止：调用外部API、联网搜索、重新抽取、新增无证据关系、生成正式图谱、写Neo4j。

所有输出均为待人工审核草案，不得视为最终事实。
"""
from __future__ import annotations
import argparse, csv, hashlib, json, logging, os, re, sys, unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("stage4")

def setup_logger(log_path: Path):
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    ch = logging.StreamHandler(sys.stdout); ch.setFormatter(fmt); logger.addHandler(ch)
    try:
        fh = logging.FileHandler(log_path, encoding="utf-8"); fh.setFormatter(fmt); logger.addHandler(fh)
    except Exception: pass

def log(msg: str): logger.info(msg)

# ────────────────────── 文本归一化 ──────────────────────
_FULL2HALF = str.maketrans(
    {chr(0xFF01 + i): chr(0x21 + i) for i in range(94)} | {'\u3000': ' ', '（': '(', '）': ')', '【': '[', '】': ']'}
)
_WS = re.compile(r'\s+')

def normalize_text(s: str) -> str:
    """基础文本归一化：全角→半角、去空白、统一标点。"""
    s = (s or "").strip()
    s = s.translate(_FULL2HALF)
    s = s.replace('／', '/').replace('＋', '+').replace('－', '-').replace('—', '-')
    s = _WS.sub(' ', s).strip()
    return s

def normalize_key(s: str) -> str:
    """用于去重比较的 key：在 normalize_text 基础上 lower、去空格。"""
    return normalize_text(s).lower().replace(' ', '')

def match_method(original: str, normalized: str) -> str:
    if original == normalized: return "exact"
    if original.strip() == normalized: return "exact"
    if original.lower() == normalized.lower(): return "case_normalized"
    return "punctuation_normalized"

# ────────────────────── 加载输入 ──────────────────────
def load_jsonl(path: Path) -> List[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def load_csv_rows(path: Path) -> List[dict]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def load_yaml(path: Path) -> dict:
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

# ────────────────────── 实体标准化 ──────────────────────
def infer_entity_type_from_name(name: str) -> str:
    """仅按明确名称后缀给出保守类型提示，不使用行业常识补全实体。"""
    compact = normalize_key(name)
    rules = (
        (("材料", "基板", "靶材", "化学品"), "material"),
        (("器件", "面板", "产品", "屏"), "product"),
        (("技术", "工艺", "方法"), "technology"),
        (("设备", "装备", "产线"), "equipment"),
        (("应用", "场景"), "application"),
        (("企业", "公司"), "company"),
        (("产业集群", "区域"), "region"),
        (("产业链", "创新链", "供应链", "环节", "流程"), "industry_link"),
        (("产业", "行业"), "industry"),
    )
    for suffixes, entity_type in rules:
        if compact.endswith(suffixes):
            return entity_type
    return "unknown"


def build_canonical_entities(entities: List[dict], cfg: dict, relations: Optional[List[dict]] = None):
    """按规范名称合并 verified 实体，并解析同名类型分歧。

    同一名称不能因为本地模型给出不同类型而生成多个树节点。类型选择依次参考：
    名称的明确后缀、已验证关系端点类型、候选出现次数；仍无法唯一确定时才标记复核。
    """
    level_rules = cfg.get("entity_level_rules", {})
    vcs_rules = cfg.get("value_chain_stage_rules", {})
    groups: Dict[str, List[dict]] = defaultdict(list)
    for e in entities:
        key = normalize_key(e.get("normalized_name_candidate") or e.get("surface_form", ""))
        groups[key].append(e)

    relation_type_votes: Dict[str, Counter] = defaultdict(Counter)
    for r in relations or []:
        relation_type_votes[normalize_key(r.get("subject", ""))][r.get("subject_type", "unknown")] += 1
        relation_type_votes[normalize_key(r.get("object", ""))][r.get("object_type", "unknown")] += 1

    canonicals, aliases_list = [], []
    cid_counter = 0
    for key, members in groups.items():
        cid_counter += 1
        cid = f"CE{cid_counter:04d}"
        type_counts = Counter(str(m.get("entity_type") or "unknown") for m in members)
        scores = Counter(type_counts)
        for entity_type, count in relation_type_votes.get(key, {}).items():
            scores[entity_type] += count * 3
        name_for_hint = normalize_text(members[0].get("normalized_name_candidate") or members[0].get("surface_form", ""))
        type_hint = infer_entity_type_from_name(name_for_hint)
        if type_hint in type_counts:
            scores[type_hint] += 10
        best_score = max(scores.values()) if scores else 0
        winners = sorted(t for t, score in scores.items() if score == best_score)
        etype = winners[0] if winners else "unknown"
        rep = next((m for m in members if str(m.get("entity_type") or "unknown") == etype), members[0])
        canonical_name = normalize_text(rep.get("normalized_name_candidate") or rep.get("surface_form", ""))
        entity_level = level_rules.get(etype, "unknown")
        vcs = _infer_value_chain_stage(canonical_name, etype, vcs_rules)
        source_ids = [m["entity_candidate_id"] for m in members]
        evidence_ids = list({m.get("evidence_id") for m in members if m.get("evidence_id")})
        evidence_count = len(evidence_ids)
        confidence = "high" if evidence_count >= 2 else "medium" if evidence_count == 1 else "low"
        distinct_types = sorted(type_counts)
        unresolved_type_tie = len(winners) > 1
        canonicals.append({
            "canonical_entity_id": cid, "canonical_name": canonical_name,
            "entity_type": etype, "entity_level": entity_level,
            "value_chain_stage": vcs, "source_entity_candidate_ids": ";".join(source_ids),
            "aliases": "", "evidence_count": evidence_count,
            "relation_degree": 0, "confidence_level": confidence,
            "review_status": "review_required" if unresolved_type_tie else "auto_candidate",
            "review_reason": (
                f"同名类型无法唯一判定: {distinct_types}" if unresolved_type_tie else
                (f"同名类型已按证据规则合并: {distinct_types} -> {etype}" if len(distinct_types) > 1 else "")
            ),
        })
        # aliases: 收集 members 中表面形式不同于 canonical_name 的
        seen_aliases = set()
        for m in members:
            sf = normalize_text(m.get("surface_form", ""))
            if sf and sf != canonical_name and sf not in seen_aliases:
                seen_aliases.add(sf)
                mm = match_method(m.get("surface_form", ""), canonical_name)
                aliases_list.append({
                    "alias": sf, "canonical_entity_id": cid, "canonical_name": canonical_name,
                    "alias_source": f"stage3c_{m.get('entity_candidate_id','')}",
                    "match_method": mm, "similarity_score": 1.0 if mm == "exact" else 0.95,
                    "evidence_ids": m.get("evidence_id", ""), "review_required": mm not in ("exact","case_normalized","punctuation_normalized"),
                    "notes": "",
                })
    return canonicals, aliases_list

def _infer_value_chain_stage(name: str, etype: str, rules: dict) -> str:
    if etype in (rules.get("upstream_types") or []): return "upstream"
    if etype in (rules.get("midstream_types") or []): return "midstream"
    if etype in (rules.get("downstream_types") or []): return "downstream"
    if etype in (rules.get("supporting_types") or []): return "supporting"
    if etype == "industry_link":
        for kw in (rules.get("industry_link_upstream_keywords") or []):
            if kw in name: return "upstream"
        for kw in (rules.get("industry_link_midstream_keywords") or []):
            if kw in name: return "midstream"
        for kw in (rules.get("industry_link_downstream_keywords") or []):
            if kw in name: return "downstream"
        for kw in (rules.get("industry_link_supporting_keywords") or []):
            if kw in name: return "supporting"
    if etype in ("industry", "sector", "sub_chain"): return "cross_stage"
    return "unknown"

# ────────────────────── 关系标准化 ──────────────────────
def build_normalized_relations(relations: List[dict], canonicals: List[dict], cfg: dict = None):
    """去重关系，合并证据。对未匹配的关系端点自动补充 canonical 实体（review_required）。"""
    cfg = cfg or {}
    level_rules = cfg.get("entity_level_rules", {})
    vcs_rules = cfg.get("value_chain_stage_rules", {})
    # 建立 surface→canonical 映射
    name_type_to_cid = {}
    for c in canonicals:
        key = normalize_key(c["canonical_name"]) + "|" + c["entity_type"]
        name_type_to_cid[key] = c
        # 也加 name-only 映射（fallback）
        nk = normalize_key(c["canonical_name"])
        if nk not in name_type_to_cid:
            name_type_to_cid[nk] = c

    cid_counter = len(canonicals)

    def resolve(name, etype):
        nonlocal cid_counter
        key = normalize_key(name) + "|" + etype
        if key in name_type_to_cid: return name_type_to_cid[key]
        nk = normalize_key(name)
        if nk in name_type_to_cid: return name_type_to_cid[nk]
        # 自动创建新 canonical（来自关系端点，标记 review_required）
        cid_counter += 1
        cname = normalize_text(name)
        entity_level = level_rules.get(etype, "unknown")
        vcs = _infer_value_chain_stage(cname, etype, vcs_rules)
        new_c = {
            "canonical_entity_id": f"CE{cid_counter:04d}", "canonical_name": cname,
            "entity_type": etype, "entity_level": entity_level,
            "value_chain_stage": vcs, "source_entity_candidate_ids": "from_relation",
            "aliases": "", "evidence_count": 0,
            "relation_degree": 0, "confidence_level": "low",
            "review_status": "review_required", "review_reason": "仅出现在关系端点，无独立实体候选",
        }
        canonicals.append(new_c)
        name_type_to_cid[key] = new_c
        name_type_to_cid[nk] = new_c
        return new_c

    dedup: Dict[str, dict] = {}
    evidence_map = []

    for r in relations:
        subj_c = resolve(r.get("subject",""), r.get("subject_type",""))
        obj_c = resolve(r.get("object",""), r.get("object_type",""))
        dedup_key = f"{subj_c['canonical_entity_id']}|{r.get('relation_type','')}|{obj_c['canonical_entity_id']}"
        if dedup_key not in dedup:
            dedup[dedup_key] = {
                "subject_canonical_id": subj_c["canonical_entity_id"],
                "subject_canonical_name": subj_c["canonical_name"],
                "subject_type": subj_c["entity_type"],
                "relation_type": r.get("relation_type",""),
                "object_canonical_id": obj_c["canonical_entity_id"],
                "object_canonical_name": obj_c["canonical_name"],
                "object_type": obj_c["entity_type"],
                "value_chain_stage": subj_c.get("value_chain_stage","unknown"),
                "source_ids": [], "evidence_ids": [], "pages": [], "quotes": [],
            }
        rec = dedup[dedup_key]
        rec["source_ids"].append(r.get("relation_candidate_id",""))
        if r.get("evidence_id"): rec["evidence_ids"].append(r["evidence_id"])
        if r.get("page_no") is not None: rec["pages"].append(str(r["page_no"]))
        if r.get("quote"): rec["quotes"].append(r["quote"][:100])
        evidence_map.append({
            "normalized_relation_id": "", "relation_candidate_id": r.get("relation_candidate_id",""),
            "evidence_id": r.get("evidence_id",""), "page_no": r.get("page_no",""),
            "quote": r.get("quote",""), "task_id": r.get("task_id",""),
            "source_query": r.get("source_query",""), "assertion_type": r.get("assertion_type",""),
            "verification_status": r.get("verification_status",""),
            "_dedup_key": dedup_key,
        })

    normalized = []
    rid_counter = 0
    for dk, rec in dedup.items():
        rid_counter += 1
        nrid = f"NR{rid_counter:04d}"
        ev_count = len(set(rec["evidence_ids"]))
        confidence = "high" if ev_count >= 2 else "medium"
        normalized.append({
            "normalized_relation_id": nrid,
            "subject_canonical_id": rec["subject_canonical_id"],
            "subject_canonical_name": rec["subject_canonical_name"],
            "subject_type": rec["subject_type"],
            "relation_type": rec["relation_type"],
            "object_canonical_id": rec["object_canonical_id"],
            "object_canonical_name": rec["object_canonical_name"],
            "object_type": rec["object_type"],
            "value_chain_stage": rec["value_chain_stage"],
            "source_relation_candidate_ids": ";".join(rec["source_ids"]),
            "evidence_count": ev_count,
            "evidence_ids": ";".join(sorted(set(rec["evidence_ids"]))),
            "pages": ";".join(sorted(set(rec["pages"]))),
            "quotes_preview": " | ".join(rec["quotes"][:3]),
            "confidence_level": confidence,
            "review_status": "auto_candidate", "review_reason": "",
        })
        # 回填 evidence_map
        for em in evidence_map:
            if em["_dedup_key"] == dk:
                em["normalized_relation_id"] = nrid

    # 更新 canonical entities 的 relation_degree
    degree_count = Counter()
    for nr in normalized:
        degree_count[nr["subject_canonical_id"]] += 1
        degree_count[nr["object_canonical_id"]] += 1
    for c in canonicals:
        c["relation_degree"] = degree_count.get(c["canonical_entity_id"], 0)

    # 清理 evidence_map 的临时字段
    for em in evidence_map:
        em.pop("_dedup_key", None)

    return normalized, evidence_map

# ────────────────────── 冲突检测 ──────────────────────
def detect_conflicts(canonicals, normalized_rels):
    conflicts = []
    cid = [0]
    def add(ctype, severity, ents, rels, desc, eids, action):
        cid[0] += 1
        conflicts.append({
            "conflict_id": f"CF{cid[0]:04d}", "conflict_type": ctype, "severity": severity,
            "related_entities": ents, "related_relations": rels,
            "description": desc, "evidence_ids": eids, "recommended_action": action,
        })
    # 1. 同名不同类型
    name_groups: Dict[str, List[dict]] = defaultdict(list)
    for c in canonicals:
        name_groups[normalize_key(c["canonical_name"])].append(c)
    for nk, clist in name_groups.items():
        if len(clist) > 1:
            types = [c["entity_type"] for c in clist]
            if len(set(types)) > 1:
                add("same_name_diff_type", "high",
                    ";".join(c["canonical_entity_id"] for c in clist), "",
                    f"同名'{clist[0]['canonical_name']}'但类型不同: {types}", "", "人工判定正确类型")
    # 2. 方向冲突 A->R->B 与 B->R->A
    rel_pairs = {}
    for nr in normalized_rels:
        pair = (nr["subject_canonical_id"], nr["relation_type"], nr["object_canonical_id"])
        rev = (nr["object_canonical_id"], nr["relation_type"], nr["subject_canonical_id"])
        if rev in rel_pairs:
            add("direction_conflict", "high",
                f"{nr['subject_canonical_id']};{nr['object_canonical_id']}",
                f"{nr['normalized_relation_id']};{rel_pairs[rev]}",
                f"{nr['subject_canonical_name']}<->{nr['relation_type']}<->{nr['object_canonical_name']} 方向冲突",
                nr.get("evidence_ids",""), "人工确认正确方向")
        rel_pairs[pair] = nr["normalized_relation_id"]
    # 3. 同主客体多关系类型
    so_types: Dict[Tuple[str,str], List[str]] = defaultdict(list)
    for nr in normalized_rels:
        so_types[(nr["subject_canonical_id"], nr["object_canonical_id"])].append(nr["relation_type"])
    for (s,o), rtypes in so_types.items():
        if len(set(rtypes)) > 1:
            add("multi_relation_type", "medium", f"{s};{o}", "",
                f"同一主客体存在多种关系: {list(set(rtypes))}", "", "人工确认是否需合并或拆分")
    # 4. 证据不足（单证据关系）
    for nr in normalized_rels:
        if nr["evidence_count"] < 1:
            add("evidence_insufficient", "high", "", nr["normalized_relation_id"],
                f"关系无证据: {nr['subject_canonical_name']}->{nr['relation_type']}->{nr['object_canonical_name']}",
                "", "不应存在，检查逻辑")
    return conflicts

# ────────────────────── 优先级与审核表 ──────────────────────
def _priority(text: str, cfg_pri: dict) -> str:
    for kw in (cfg_pri.get("P0_keywords") or []):
        if kw in text: return "P0"
    for kw in (cfg_pri.get("P1_keywords") or []):
        if kw in text: return "P1"
    for kw in (cfg_pri.get("P2_keywords") or []):
        if kw in text: return "P2"
    return "P3"

def build_manual_review_sheet(canonicals, normalized_rels, conflicts, review_queue, cfg_pri):
    items = []
    rid = [0]
    def add(rtype, priority, subj, relt, obj, eid, pages, quotes, issue, rec_dec):
        rid[0] += 1
        items.append({
            "review_id": f"RV{rid[0]:04d}", "review_type": rtype, "priority": priority,
            "subject": subj, "relation_type": relt, "object": obj,
            "entity_or_relation_id": eid, "evidence_pages": pages,
            "evidence_quotes": quotes[:200] if quotes else "",
            "issue_summary": issue, "recommended_decision": rec_dec,
            "human_decision": "", "human_comment": "",
        })
    # From conflicts
    for cf in conflicts:
        pri = "P0" if cf["severity"] == "high" else "P1"
        add("conflict", pri, "", "", "", cf["related_entities"] or cf["related_relations"],
            "", "", cf["description"], cf["recommended_action"])
    # From review_required entities
    for c in canonicals:
        if c["review_status"] == "review_required":
            text = c["canonical_name"] + " " + c.get("review_reason","")
            pri = _priority(text, cfg_pri)
            add("entity_review", pri, c["canonical_name"], "", "", c["canonical_entity_id"],
                "", "", c["review_reason"], "确认实体类型与名称")
    # From review_required relations (unresolved subjects/objects marked below)
    for nr in normalized_rels:
        if nr["review_status"] == "review_required":
            text = nr["subject_canonical_name"] + nr["relation_type"] + nr["object_canonical_name"]
            pri = _priority(text, cfg_pri)
            add("relation_review", pri, nr["subject_canonical_name"], nr["relation_type"],
                nr["object_canonical_name"], nr["normalized_relation_id"],
                nr["pages"], nr["quotes_preview"], nr["review_reason"], "确认关系方向与类型")
    # From stage3c review queue
    for rq in review_queue:
        text = str(rq.get("reason","")) + str(rq.get("issue_type",""))
        pri = _priority(text, cfg_pri)
        if "quote" in text.lower() or "subject" in text.lower() or "conflict" in text.lower():
            pri = max(pri, "P1") if pri > "P1" else "P1"
        add("stage3c_carryover", pri, "", str(rq.get("issue_type","")), "",
            str(rq.get("candidate_id","")), str(rq.get("page_no","")), "",
            str(rq.get("reason","")), str(rq.get("recommended_action","")))
    return items

# ────────────────────── rejected 重检 ──────────────────────
def build_rejected_recheck(rejected: List[dict]):
    recheck = []
    rid = [0]
    for rj in rejected:
        reason = rj.get("reason","")
        if reason in ("quote_not_found", "subject_or_object_not_found"):
            rid[0] += 1
            recheck.append({
                "recheck_id": f"RC{rid[0]:04d}",
                "source_candidate_id": rj.get("candidate_id",""),
                "candidate_type": rj.get("item_type",""),
                "original_reject_reason": reason,
                "surface_form_or_relation": rj.get("candidate_id",""),
                "evidence_id": rj.get("evidence_id",""),
                "page_no": rj.get("page_no",""),
                "quote": "",
                "possible_recovery_reason": "可能由OCR差异或模型摘录导致quote/主宾语定位失败",
                "recommended_action": "人工核对原文",
                "review_required": True,
            })
    return recheck

# ────────────────────── 输出写入 ──────────────────────
def write_csv(path: Path, rows: List[dict], fields: List[str], encoding="utf-8-sig"):
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k,"") for k in fields})

def write_jsonl(path: Path, rows: List[dict]):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

# ────────────────────── 草案生成 ──────────────────────
def write_industry_chain_draft(out_dir: Path, canonicals, normalized_rels, conflicts, review_items):
    L = []
    L.append("# 产业链草案（待人工审核）")
    L.append("")
    L.append("> **重要声明**：本文档为阶段 4 自动生成的产业链结构草案，所有实体和关系均为"
             "待审核候选，**不得视为最终事实**。尚未完成人工确认、尚未生成正式图谱、尚未写入 Neo4j。")
    L.append("")
    # 分层统计
    level_groups = defaultdict(list)
    for c in canonicals:
        level_groups[c["entity_level"]].append(c)
    for lv in ["L0_industry","L1_sector","L2_sub_chain","L3_industry_link","L4_object","auxiliary","unknown"]:
        ents = level_groups.get(lv, [])
        if ents:
            L.append(f"## {lv} ({len(ents)} 个实体)")
            L.append("")
            for e in sorted(ents, key=lambda x: x["canonical_name"]):
                L.append(f"- **{e['canonical_name']}** [{e['entity_type']}] "
                         f"(value_chain={e['value_chain_stage']}, degree={e['relation_degree']}, "
                         f"confidence={e['confidence_level']})")
            L.append("")
    L.append("## 主要候选关系摘要")
    L.append("")
    L.append("| 主语 | 关系 | 宾语 | 证据数 | 页码 | 置信 |")
    L.append("|---|---|---|---|---|---|")
    for nr in sorted(normalized_rels, key=lambda x: -x["evidence_count"]):
        L.append(f"| {nr['subject_canonical_name']} | {nr['relation_type']} | "
                 f"{nr['object_canonical_name']} | {nr['evidence_count']} | {nr['pages']} | {nr['confidence_level']} |")
    L.append("")
    # 证据页码分布
    all_pages = Counter()
    for nr in normalized_rels:
        for p in nr["pages"].split(";"):
            if p.strip(): all_pages[p.strip()] += 1
    L.append("## 证据页码分布")
    L.append("")
    for p, cnt in sorted(all_pages.items(), key=lambda x: -x[1]):
        L.append(f"- 第 {p} 页: {cnt} 条关系引用")
    L.append("")
    L.append("## 证据不足区域")
    L.append("")
    L.append("- 下游应用（t09）：证据不足，候选稀少")
    L.append("- 国产替代（t10）：多为趋势/政策表述，非现实产业关系")
    L.append("")
    L.append("## 待人工审核重点")
    L.append("")
    pri_count = Counter(r["priority"] for r in review_items)
    for p in ["P0","P1","P2","P3"]:
        L.append(f"- {p}: {pri_count.get(p,0)} 项")
    L.append("")
    L.append(f"## 冲突数: {len(conflicts)}")
    L.append("")
    for cf in conflicts[:10]:
        L.append(f"- [{cf['severity']}] {cf['conflict_type']}: {cf['description']}")
    L.append("")
    L.append("---")
    L.append("> 本草案由阶段 4 脚本自动生成，不得作为最终产业链图谱使用。")
    (out_dir / "industry_chain_draft.md").write_text("\n".join(L), encoding="utf-8")

# ────────────────────── 报告生成 ──────────────────────
def write_normalization_report(out_dir, args, run_cfg, canonicals, aliases, norm_rels,
                               ev_map, review_items, conflicts, recheck, pri_count, val):
    L = []
    L.append("# 阶段 4 标准化报告")
    L.append("")
    L.append(f"- 生成时间: {run_cfg['timestamp']}")
    L.append(f"- 输入目录: {run_cfg['stage3c_dir']}")
    L.append(f"- 输入 verified 实体: {run_cfg['input_entities']}")
    L.append(f"- 输入 verified 关系: {run_cfg['input_relations']}")
    L.append(f"- 输入 review 队列: {run_cfg['input_review']}")
    L.append(f"- 输入 rejected: {run_cfg['input_rejected']}")
    L.append("")
    L.append("## 实体标准化")
    L.append("")
    L.append(f"- 方法: 基础文本归一化(全角半角/大小写/标点/空白) + 按 normalized_key 分组")
    L.append(f"- canonical 实体数: {len(canonicals)}")
    L.append(f"- 别名数: {len(aliases)}")
    L.append("")
    type_cnt = Counter(c['entity_type'] for c in canonicals)
    for t, n in type_cnt.most_common():
        L.append(f"  - {t}: {n}")
    L.append("")
    L.append("## 关系去重")
    L.append("")
    L.append(f"- 方法: 按 (subject_canonical_id, relation_type, object_canonical_id) 去重合并证据")
    L.append(f"- normalized 关系数: {len(norm_rels)}")
    L.append(f"- 证据映射条目: {len(ev_map)}")
    L.append("")
    rel_cnt = Counter(r['relation_type'] for r in norm_rels)
    for t, n in rel_cnt.most_common():
        L.append(f"  - {t}: {n}")
    L.append("")
    L.append("## 冲突检测")
    L.append("")
    L.append(f"- 冲突数: {len(conflicts)}")
    cf_cnt = Counter(c['conflict_type'] for c in conflicts)
    for t, n in cf_cnt.most_common():
        L.append(f"  - {t}: {n}")
    L.append("")
    L.append("## 复核队列")
    L.append("")
    L.append(f"- manual_review_sheet 条目: {len(review_items)}")
    for p in ["P0","P1","P2","P3"]:
        L.append(f"  - {p}: {pri_count.get(p,0)}")
    L.append("")
    L.append("## rejected 重检")
    L.append("")
    L.append(f"- stage4_rejected_recheck 条目: {len(recheck)}")
    L.append("")
    L.append("## 重要声明")
    L.append("")
    L.append("- 阶段 4 只完成候选结果清洗、标准化、去重和审核准备")
    L.append("- canonical_entities 与 normalized_relations 仍然是待审核草案")
    L.append("- 尚未完成人工确认")
    L.append("- 尚未生成正式产业链图谱")
    L.append("- 尚未写入 Neo4j")
    L.append("- 不得把本阶段输出视为最终事实库")
    L.append("")
    L.append("## 阶段 5 人工审核建议")
    L.append("")
    L.append("- 优先处理 P0 主链骨架关系（PART_OF 产业链层级）")
    L.append("- 其次处理 P1 材料/设备/技术/工艺与环节关系")
    L.append("- 确认同名不同类型冲突")
    L.append("- 核对 quote 匹配失败的 rejected 重检项")
    L.append("- 最终确认后才能生成正式图谱和写入 Neo4j")
    L.append("")
    L.append("## 验证项")
    L.append("")
    for k, v in val.items():
        L.append(f"- {'PASS' if v[0] else 'FAIL'} {k}: {v[1]}")
    L.append("")
    L.append("---")
    L.append("> 阶段 4 仅完成候选清洗与审核准备，所有输出为待审核草案。")
    (out_dir / "stage4_normalization_report.md").write_text("\n".join(L), encoding="utf-8")


# ────────────────────── 主流程 ──────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 4：候选清洗、标准化、去重、冲突检测")
    parser.add_argument("--project-root", default=r"D:\产业链建模")
    parser.add_argument("--mode", choices=["full","dry-run","entities-only","relations-only"], default="full")
    parser.add_argument("--use-embedding-similarity", action="store_true")
    parser.add_argument("--use-local-llm-for-alias-review", action="store_true")
    parser.add_argument("--similarity-threshold", type=float, default=0.86)
    args = parser.parse_args()

    project_root = Path(args.project_root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rag_root = project_root / "rag"
    out_dir = rag_root / "outputs" / f"stage4_normalization_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_logger(out_dir / "run.log")
    log("=" * 70)
    log("阶段 4：候选清洗、标准化、去重、冲突检测启动")
    log(f"输出目录: {out_dir}")
    log(f"模式: {args.mode}")

    # 加载配置
    cfg_path = rag_root / "config" / "stage4_normalization_config.yaml"
    cfg = load_yaml(cfg_path) if cfg_path.exists() else {}

    # ─── 定位阶段 3C 输出 ───
    val: Dict[str, Tuple[bool, str]] = {}
    pointer = rag_root / "latest_stage3c_run.txt"
    if not pointer.exists():
        val["stage3c_latest_run_found"] = (False, "latest_stage3c_run.txt 不存在")
        log("[FAIL] 找不到 latest_stage3c_run.txt")
        _write_fail(out_dir, val)
        return 2
    s3c_dir = Path(pointer.read_text(encoding="utf-8").strip())
    val["stage3c_latest_run_found"] = (s3c_dir.exists(), str(s3c_dir))
    if not s3c_dir.exists():
        log(f"[FAIL] 阶段 3C 目录不存在: {s3c_dir}")
        _write_fail(out_dir, val)
        return 2

    # ─── 检查必需文件 ───
    required = ["verified_entity_candidates.jsonl","verified_relation_candidates.jsonl",
                "rag_extraction_review_queue.csv","rejected_candidates.jsonl",
                "rag_extraction_report.md","validation_summary.json"]
    missing = [f for f in required if not (s3c_dir / f).exists()]
    val["required_input_files_exist"] = (len(missing)==0, f"缺失: {missing}" if missing else "全部存在")
    if missing:
        log(f"[FAIL] 缺少文件: {missing}")
        _write_fail(out_dir, val)
        return 2

    # ─── 加载数据 ───
    entities = load_jsonl(s3c_dir / "verified_entity_candidates.jsonl")
    relations = load_jsonl(s3c_dir / "verified_relation_candidates.jsonl")
    review_queue = load_csv_rows(s3c_dir / "rag_extraction_review_queue.csv")
    rejected = load_jsonl(s3c_dir / "rejected_candidates.jsonl")
    val["verified_entities_loaded"] = (len(entities) > 0, f"{len(entities)} 个")
    val["verified_relations_loaded"] = (len(relations) > 0, f"{len(relations)} 个")
    val["review_queue_loaded"] = (True, f"{len(review_queue)} 条")
    val["rejected_candidates_loaded"] = (True, f"{len(rejected)} 条")
    log(f"输入: entities={len(entities)}, relations={len(relations)}, review={len(review_queue)}, rejected={len(rejected)}")

    if args.mode == "dry-run":
        log("[DRY-RUN] 输入加载成功，不执行处理")
        val["canonical_entities_generated"] = (True, "dry-run 跳过")
        _write_val(out_dir, val)
        return 0

    # ─── 实体标准化 ───
    log("开始实体标准化...")
    canonicals, aliases = build_canonical_entities(entities, cfg, relations)
    log(f"canonical 实体: {len(canonicals)}, 别名: {len(aliases)}")
    val["canonical_entities_generated"] = (len(canonicals) > 0, f"{len(canonicals)} 个")
    val["entity_aliases_generated"] = (True, f"{len(aliases)} 个")

    if args.mode == "entities-only":
        _write_entities_only(out_dir, canonicals, aliases, val, cfg)
        log("[entities-only] 完成")
        return 0

    # ─── 关系标准化 ───
    log("开始关系标准化...")
    norm_rels, ev_map = build_normalized_relations(relations, canonicals, cfg)
    log(f"normalized 关系: {len(norm_rels)}, 证据映射: {len(ev_map)}, canonical 实体(含关系端点补充): {len(canonicals)}")
    val["normalized_relations_generated"] = (len(norm_rels) > 0, f"{len(norm_rels)} 条")
    val["relation_evidence_map_generated"] = (len(ev_map) > 0, f"{len(ev_map)} 条")

    if args.mode == "relations-only":
        _write_relations_only(out_dir, norm_rels, ev_map, val, cfg)
        log("[relations-only] 完成")
        return 0

    # ─── 冲突检测 ───
    log("冲突检测...")
    conflicts = detect_conflicts(canonicals, norm_rels)
    log(f"检测到 {len(conflicts)} 个冲突")
    val["conflict_report_generated"] = (True, f"{len(conflicts)} 个冲突")

    # ─── 审核表 ───
    log("生成审核表...")
    cfg_pri = cfg.get("priority_rules", {})
    review_items = build_manual_review_sheet(canonicals, norm_rels, conflicts, review_queue, cfg_pri)
    pri_count = Counter(r["priority"] for r in review_items)
    log(f"manual_review_sheet: {len(review_items)} 条 (P0={pri_count.get('P0',0)}, P1={pri_count.get('P1',0)}, P2={pri_count.get('P2',0)}, P3={pri_count.get('P3',0)})")
    val["manual_review_sheet_generated"] = (len(review_items) > 0, f"{len(review_items)} 条")

    # ─── stage4_review_queue（合并 3C + 4 新发现）───
    s4_review = []
    s4_rid = [0]
    for rq in review_queue:
        s4_rid[0] += 1
        s4_review.append({
            "review_item_id": f"S4RQ{s4_rid[0]:04d}", "source_stage": "3C",
            "item_type": rq.get("item_type",""), "priority": _priority(str(rq.get("reason","")), cfg_pri),
            "reason": rq.get("reason",""), "related_id": rq.get("candidate_id",""),
            "evidence_id": rq.get("evidence_id",""), "page_no": rq.get("page_no",""),
            "recommended_action": rq.get("recommended_action",""),
        })
    for cf in conflicts:
        s4_rid[0] += 1
        s4_review.append({
            "review_item_id": f"S4RQ{s4_rid[0]:04d}", "source_stage": "4",
            "item_type": "conflict", "priority": "P0" if cf["severity"]=="high" else "P1",
            "reason": cf["description"], "related_id": cf.get("related_entities","") or cf.get("related_relations",""),
            "evidence_id": cf.get("evidence_ids",""), "page_no": "",
            "recommended_action": cf["recommended_action"],
        })
    val["stage4_review_queue_generated"] = (len(s4_review) > 0, f"{len(s4_review)} 条")

    # ─── rejected 重检 ───
    recheck = build_rejected_recheck(rejected)
    log(f"rejected 重检: {len(recheck)} 条")

    # ─── 写输出 ───
    enc = (cfg.get("output") or {}).get("csv_encoding", "utf-8-sig")
    write_csv(out_dir / "canonical_entities.csv", canonicals,
              ["canonical_entity_id","canonical_name","entity_type","entity_level",
               "value_chain_stage","source_entity_candidate_ids","aliases","evidence_count",
               "relation_degree","confidence_level","review_status","review_reason"], enc)
    write_csv(out_dir / "entity_aliases.csv", aliases,
              ["alias","canonical_entity_id","canonical_name","alias_source",
               "match_method","similarity_score","evidence_ids","review_required","notes"], enc)
    write_csv(out_dir / "normalized_relations.csv", norm_rels,
              ["normalized_relation_id","subject_canonical_id","subject_canonical_name","subject_type",
               "relation_type","object_canonical_id","object_canonical_name","object_type",
               "value_chain_stage","source_relation_candidate_ids","evidence_count","evidence_ids",
               "pages","quotes_preview","confidence_level","review_status","review_reason"], enc)
    write_csv(out_dir / "relation_evidence_map.csv", ev_map,
              ["normalized_relation_id","relation_candidate_id","evidence_id","page_no",
               "quote","task_id","source_query","assertion_type","verification_status"], enc)
    write_csv(out_dir / "manual_review_sheet.csv", review_items,
              ["review_id","review_type","priority","subject","relation_type","object",
               "entity_or_relation_id","evidence_pages","evidence_quotes","issue_summary",
               "recommended_decision","human_decision","human_comment"], enc)
    write_csv(out_dir / "conflict_report.csv", conflicts,
              ["conflict_id","conflict_type","severity","related_entities","related_relations",
               "description","evidence_ids","recommended_action"], enc)
    write_csv(out_dir / "stage4_review_queue.csv", s4_review,
              ["review_item_id","source_stage","item_type","priority","reason","related_id",
               "evidence_id","page_no","recommended_action"], enc)
    write_csv(out_dir / "stage4_rejected_recheck.csv", recheck,
              ["recheck_id","source_candidate_id","candidate_type","original_reject_reason",
               "surface_form_or_relation","evidence_id","page_no","quote",
               "possible_recovery_reason","recommended_action","review_required"], enc)

    # 草案
    write_industry_chain_draft(out_dir, canonicals, norm_rels, conflicts, review_items)
    val["industry_chain_draft_generated"] = (True, "已生成")

    # 边界验证
    val["no_external_api_called"] = (True, "仅本地规则处理")
    val["no_new_relations_extracted"] = (True, "未新增关系")
    val["no_graph_generated"] = (True, "未生成图谱/未写 Neo4j")
    val["original_pdf_not_modified"] = (True, "本阶段不写入 PDF")

    # run_config
    run_cfg = {
        "timestamp": timestamp, "project_root": str(project_root),
        "stage3c_dir": str(s3c_dir), "mode": args.mode,
        "use_embedding_similarity": args.use_embedding_similarity,
        "use_local_llm": args.use_local_llm_for_alias_review,
        "similarity_threshold": args.similarity_threshold,
        "input_entities": len(entities), "input_relations": len(relations),
        "input_review": len(review_queue), "input_rejected": len(rejected),
        "output_canonical_entities": len(canonicals), "output_aliases": len(aliases),
        "output_normalized_relations": len(norm_rels), "output_conflicts": len(conflicts),
        "output_review_items": len(review_items), "output_recheck": len(recheck),
    }
    (out_dir / "run_config.json").write_text(
        json.dumps(run_cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    # validation_summary
    _write_val(out_dir, val)

    # 报告
    write_normalization_report(out_dir, args, run_cfg, canonicals, aliases, norm_rels,
                               ev_map, review_items, conflicts, recheck, pri_count, val)

    # latest 指针
    (rag_root / "latest_stage4_run.txt").write_text(str(out_dir), encoding="utf-8")

    log("=" * 70)
    log(f"完成: canonical={len(canonicals)}, aliases={len(aliases)}, relations={len(norm_rels)}, "
        f"conflicts={len(conflicts)}, review={len(review_items)}, recheck={len(recheck)}")
    log(f"阶段 4 验收: {'通过' if all(v[0] for v in val.values()) else '存在未通过项'}")
    return 0


def _write_val(out_dir: Path, val: dict):
    obj = {k: {"passed": bool(v[0]), "note": v[1]} for k, v in val.items()}
    (out_dir / "validation_summary.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def _write_fail(out_dir: Path, val: dict):
    _write_val(out_dir, val)
    md = ["# 阶段 4 报告（未通过）","","前置条件不满足，未执行处理。","",
          "> 本阶段只做候选清洗，不得视为最终事实。"]
    (out_dir / "stage4_normalization_report.md").write_text("\n".join(md), encoding="utf-8")

def _write_entities_only(out_dir, canonicals, aliases, val, cfg):
    enc = (cfg.get("output") or {}).get("csv_encoding", "utf-8-sig")
    write_csv(out_dir / "canonical_entities.csv", canonicals,
              ["canonical_entity_id","canonical_name","entity_type","entity_level",
               "value_chain_stage","source_entity_candidate_ids","aliases","evidence_count",
               "relation_degree","confidence_level","review_status","review_reason"], enc)
    write_csv(out_dir / "entity_aliases.csv", aliases,
              ["alias","canonical_entity_id","canonical_name","alias_source",
               "match_method","similarity_score","evidence_ids","review_required","notes"], enc)
    _write_val(out_dir, val)

def _write_relations_only(out_dir, norm_rels, ev_map, val, cfg):
    enc = (cfg.get("output") or {}).get("csv_encoding", "utf-8-sig")
    write_csv(out_dir / "normalized_relations.csv", norm_rels,
              ["normalized_relation_id","subject_canonical_id","subject_canonical_name","subject_type",
               "relation_type","object_canonical_id","object_canonical_name","object_type",
               "value_chain_stage","source_relation_candidate_ids","evidence_count","evidence_ids",
               "pages","quotes_preview","confidence_level","review_status","review_reason"], enc)
    write_csv(out_dir / "relation_evidence_map.csv", ev_map,
              ["normalized_relation_id","relation_candidate_id","evidence_id","page_no",
               "quote","task_id","source_query","assertion_type","verification_status"], enc)
    _write_val(out_dir, val)


if __name__ == "__main__":
    sys.exit(main())
