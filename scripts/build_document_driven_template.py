#!/usr/bin/env python3
"""
从已审核实体、关系、章节标题和证据片段构建 document_driven 动态产业链树。

本脚本只使用本地文件和可选本地 Ollama /api/generate；LLM 结果仅作为候选建议，
最终输出必须通过程序校验后才写入层级树文件。
"""
import argparse
import csv
import hashlib
import json
import logging
import re
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

FORBIDDEN_TREE_LABELS = {"上游", "中游", "下游", "上下游", "上中下游", "产业链上下游"}
FORBIDDEN_L1 = FORBIDDEN_TREE_LABELS
OVERLY_GENERIC_TERMS = {
    "产业", "产业链", "发展", "现状", "趋势", "市场", "政策", "分析", "概述", "情况", "分布",
    "材料", "原料", "产品", "工艺", "技术", "核心技术", "关键原材料", "配套材料",
}
EXCLUDED_TREE_ENTITY_TYPES = {"company"}
ENTITY_BRANCHES = [
    ("material", "材料", "material_group", {"material"}),
    ("equipment", "设备", "equipment_group", {"equipment"}),
    ("technology", "技术工具", "technology_group", {"technology", "standard", "platform"}),
    ("application", "产品与应用", "application_group", {"application", "scenario", "product"}),
]


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_pointer(path: Path) -> Path | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return Path(text) if text else None


def latest_stage_dir(project_root: Path, pointer_name: str) -> Path | None:
    return resolve_pointer(project_root / "rag" / pointer_name)


def find_parsed_dir(project_root: Path) -> Path | None:
    pointed = resolve_pointer(project_root / "parsed_documents" / "latest_run.txt")
    if pointed and pointed.exists():
        return pointed
    parsed_root = project_root / "parsed_documents"
    candidates = [p for p in parsed_root.iterdir() if p.is_dir()] if parsed_root.exists() else []
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def find_parsed_dirs(project_root: Path, doc_ids: list[str]) -> list[Path]:
    parsed_root = project_root / "parsed_documents"
    if not doc_ids:
        single = find_parsed_dir(project_root)
        return [single] if single else []
    out = []
    for doc_id in doc_ids:
        direct = parsed_root / doc_id
        candidates = []
        if direct.exists():
            candidates.append(direct)
        if parsed_root.exists():
            candidates.extend(
                p for p in parsed_root.iterdir()
                if p.is_dir() and p.name.startswith(f"{doc_id}_")
            )
        valid = [
            p for p in candidates
            if (p / "document_structure.json").exists() and (p / "evidence_chunks.jsonl").exists()
        ]
        if valid:
            valid.sort(key=lambda p: (p.name == doc_id, p.stat().st_mtime), reverse=True)
            out.append(valid[0])
    return out


def load_inputs(project_root: Path) -> dict[str, Any]:
    s46 = latest_stage_dir(project_root, "latest_stage4_6_run.txt")
    s45 = latest_stage_dir(project_root, "latest_stage4_5_run.txt")
    s4 = latest_stage_dir(project_root, "latest_stage4_run.txt")
    source = s46 or s45 or s4
    if not source:
        source = project_root / "rag" / "outputs"

    if s46:
        # Keep all datasets on the same approval boundary.  An intentionally
        # empty approved relation file must not be silently replaced by an
        # earlier unreviewed draft.
        entities = read_csv_rows(s46 / "approved_entities.csv")
        relations = read_csv_rows(s46 / "approved_relations.csv")
        aliases = read_csv_rows(s46 / "approved_entity_aliases.csv")
        relation_evidence = read_csv_rows(s46 / "approved_relation_evidence_map.csv")
    elif s45:
        entities = read_csv_rows(s45 / "conservative_canonical_entities.csv")
        relations = read_csv_rows(s45 / "conservative_normalized_relations.csv")
        aliases = []
        relation_evidence = read_csv_rows(s45 / "conservative_relation_evidence_map.csv")
    elif s4:
        entities = read_csv_rows(s4 / "canonical_entities.csv")
        relations = read_csv_rows(s4 / "normalized_relations.csv")
        aliases = read_csv_rows(s4 / "entity_aliases.csv")
        relation_evidence = read_csv_rows(s4 / "relation_evidence_map.csv")
    else:
        entities, relations, aliases, relation_evidence = [], [], [], []

    run_config = read_json(source / "run_config.json") if source else {}
    doc_ids = run_config.get("allowed_doc_ids") or []
    if not doc_ids and run_config.get("doc_id"):
        doc_ids = [run_config.get("doc_id")]
    parsed_dirs = find_parsed_dirs(project_root, [str(x) for x in doc_ids if x])
    parsed_dir = parsed_dirs[0] if parsed_dirs else None
    document_structure = {"sections": []}
    evidence_chunks = []
    for pdir in parsed_dirs:
        ds = read_json(pdir / "document_structure.json")
        for sec in ds.get("sections", []):
            row = dict(sec)
            row["_doc_id"] = ds.get("doc_id", "")
            document_structure["sections"].append(row)
        with open(pdir / "evidence_chunks.jsonl", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evidence_chunks.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return {
        "stage_source_dir": source,
        "parsed_dir": parsed_dir,
        "parsed_dirs": parsed_dirs,
        "entities": entities,
        "relations": relations,
        "aliases": aliases,
        "relation_evidence": relation_evidence,
        "document_structure": document_structure,
        "evidence_chunks": evidence_chunks,
    }


def split_evidence_ids(text: str) -> list[str]:
    if not text:
        return []
    return [p.strip() for p in re.split(r"[;,|]", text) if p.strip()]


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def clean_label(label: str) -> str:
    label = re.sub(r"^\s*\d+(\.\d+)*\s*", "", label or "").strip()
    label = re.sub(r"\s+", "", label)
    return label


def is_generic_term(label: str) -> bool:
    label = clean_label(label)
    return label in OVERLY_GENERIC_TERMS or len(label) <= 1


def is_forbidden_tree_label(label: str) -> bool:
    compact = clean_label(label)
    return compact in FORBIDDEN_TREE_LABELS or any(
        token in compact for token in ("上中下游", "上下游", "上游", "中游", "下游")
    )


def collect_evidence_universe(inputs: dict[str, Any]) -> set[str]:
    ids = {c.get("evidence_id", "") for c in inputs["evidence_chunks"]}
    for row in inputs["relation_evidence"]:
        ids.add(row.get("evidence_id", ""))
    for rel in inputs["relations"]:
        ids.update(split_evidence_ids(rel.get("evidence_ids", "")))
    return {i for i in ids if i}


def collect_titles(inputs: dict[str, Any]) -> list[str]:
    titles = []
    for sec in inputs["document_structure"].get("sections", []):
        title = clean_label(sec.get("title", ""))
        if title:
            titles.append(title)
    for chunk in inputs["evidence_chunks"]:
        if chunk.get("content_type") in {"heading", "table", "figure"}:
            title = clean_label(chunk.get("text_normalized") or chunk.get("text_raw") or chunk.get("title_context", ""))
            if title:
                titles.append(title)
    return titles


def entity_name(row: dict[str, str]) -> str:
    return clean_label(row.get("canonical_name") or row.get("entity_name") or row.get("name") or "")


def entity_id(row: dict[str, str]) -> str:
    return row.get("approved_entity_id") or row.get("canonical_entity_id") or row.get("entity_id") or ""


def relation_id(row: dict[str, str]) -> str:
    return row.get("approved_relation_id") or row.get("normalized_relation_id") or row.get("relation_id") or ""


def build_entity_maps(inputs: dict[str, Any]) -> dict[str, Any]:
    entities = []
    by_name = {}
    by_id = {}
    for row in inputs["entities"]:
        name = entity_name(row)
        eid = entity_id(row)
        if not name or not eid:
            continue
        item = dict(row)
        item["_name"] = name
        item["_id"] = eid
        item["_type"] = row.get("entity_type", "")
        item["_evidence_count"] = safe_int(row.get("evidence_count", 0))
        item["_relation_degree"] = safe_int(row.get("relation_degree", 0))
        entities.append(item)
        by_name[name] = item
        by_id[eid] = item
    return {"entities": entities, "by_name": by_name, "by_id": by_id}


def relation_support(inputs: dict[str, Any]) -> dict[str, Any]:
    by_entity = defaultdict(list)
    by_parent_object = defaultdict(list)
    rel_evidence_map = defaultdict(list)
    for row in inputs["relation_evidence"]:
        rid = row.get("approved_relation_id") or row.get("normalized_relation_id") or row.get("relation_id") or ""
        eid = row.get("evidence_id", "")
        if rid and eid:
            rel_evidence_map[rid].append(eid)
    for rel in inputs["relations"]:
        rid = relation_id(rel)
        evs = split_evidence_ids(rel.get("evidence_ids", ""))
        evs.extend(rel_evidence_map.get(rid, []))
        rel["_evidence_ids_list"] = sorted(set(evs))
        subject = clean_label(rel.get("subject_canonical_name", ""))
        obj = clean_label(rel.get("object_canonical_name", ""))
        if subject:
            by_entity[subject].append(rel)
        if obj:
            by_entity[obj].append(rel)
            by_parent_object[obj].append(rel)
    return {"by_entity": by_entity, "by_parent_object": by_parent_object}


def evidence_for_entity(name: str, row: dict[str, Any], support: dict[str, Any]) -> list[str]:
    evs = []
    for rel in support["by_entity"].get(name, []):
        evs.extend(rel.get("_evidence_ids_list", []))
    return sorted(set(evs))


def choose_central_term(entities: list[dict[str, Any]], titles: list[str], root_label: str) -> str:
    root_clean = clean_label(root_label).replace("产业链", "").replace("产业", "")
    candidates = []
    for ent in entities:
        name = ent["_name"]
        if not name or name == root_clean or name in root_label or is_generic_term(name):
            continue
        etype = ent.get("_type", "")
        if etype not in {"product", "industry"}:
            continue
        title_hits = sum(1 for title in titles if name in title)
        score = ent["_relation_degree"] + ent["_evidence_count"] + title_hits * 2
        if etype == "product":
            score += 8
        if etype == "industry":
            score += 2
        candidates.append((score, len(name), name))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))
    return candidates[0][2]


def choose_specific_branch_entity(entities: list[dict[str, Any]], titles: list[str],
                                  root_label: str, type_key: str) -> dict[str, Any] | None:
    """Choose an evidence-derived branch label instead of a generic display label."""
    if not entities:
        return None
    root_clean = clean_label(root_label).replace("产业链", "").replace("产业", "")
    root_terms = {root_clean}
    if root_clean.startswith("新型") and len(root_clean) > 2:
        root_terms.add(root_clean[2:])
    root_terms = {term for term in root_terms if len(term) >= 2}
    suffixes = {
        "material": ("材料", "原料"),
        "equipment": ("设备", "装备", "仪器"),
        "technology": ("技术", "工艺", "平台", "工具"),
        "application": ("应用", "产品", "终端", "场景"),
        "core": ("产业", "环节", "制造", "生产", "服务"),
    }.get(type_key, ())
    ranked = []
    for ent in entities:
        name = ent.get("_name", "")
        if not name or is_generic_term(name):
            continue
        score = ent.get("_relation_degree", 0) * 2 + ent.get("_evidence_count", 0)
        score += sum(8 for term in root_terms if term in name)
        score += sum(2 for title in titles if name in title)
        if suffixes and name.endswith(suffixes):
            score += 2
        ranked.append((score, len(name), name, ent))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    return ranked[0][3]


def make_branch_candidates(inputs: dict[str, Any], template: dict[str, Any], root_label: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    maps = build_entity_maps(inputs)
    excluded_types = set(template.get("excluded_tree_entity_types", [])) | EXCLUDED_TREE_ENTITY_TYPES
    entities = [ent for ent in maps["entities"] if ent.get("_type") not in excluded_types]
    support = relation_support(inputs)
    titles = collect_titles(inputs)
    evidence_universe = collect_evidence_universe(inputs)
    used_entity_names = set()
    debug = {"candidate_first_level_nodes": [], "rejected_first_level_nodes": []}

    central = choose_central_term(entities, titles, root_label)
    branches = []

    core_types = {"industry", "industry_link", "process", "product"}
    core_entities = []
    for ent in entities:
        name = ent["_name"]
        if ent.get("_id") == "CE_ROOT" or is_generic_term(name):
            continue
        if ent.get("_type") in core_types:
            core_entities.append(ent)
    if core_entities:
        core_branch_entity = (
            maps["by_name"].get(central)
            if central else choose_specific_branch_entity(core_entities, titles, root_label, "core")
        )
        core_label = core_branch_entity["_name"] if core_branch_entity else ""
        if core_label:
            core_children = [ent for ent in core_entities if ent.get("_id") != core_branch_entity.get("_id")]
            branches.append({
                "label": core_label,
                "category": "main_chain",
                "entities": core_children,
                "all_entities": core_entities,
                "branch_entity": core_branch_entity,
                "type_key": "core",
                "fallback_display_schema": False,
            })

    for type_key, label, category, accepted in ENTITY_BRANCHES:
        ents = []
        for ent in entities:
            if ent.get("_type") not in accepted:
                continue
            if type_key == "application" and ent["_name"] == central:
                continue
            if is_generic_term(ent["_name"]):
                continue
            ents.append(ent)
        if ents:
            branch_entity = choose_specific_branch_entity(ents, titles, root_label, type_key)
            if not branch_entity:
                continue
            branches.append({
                "label": branch_entity["_name"],
                "category": category,
                "entities": [ent for ent in ents if ent.get("_id") != branch_entity.get("_id")],
                "all_entities": ents,
                "branch_entity": branch_entity,
                "type_key": type_key,
                "fallback_display_schema": False,
            })

    if len(branches) < 4 and template.get("allow_fallback_display_schema", False):
        for node in template.get("fallback_display_schema_nodes", []):
            label = node.get("label", "")
            if label in {b["label"] for b in branches} or is_forbidden_tree_label(label):
                continue
            accepted = set(node.get("accept_entity_types", []))
            if accepted and accepted.issubset(excluded_types):
                debug["rejected_first_level_nodes"].append({
                    "label": label,
                    "reason": "excluded_tree_entity_type",
                    "accepted_entity_types": sorted(accepted),
                })
                continue
            ents = [ent for ent in entities if ent.get("_type") in accepted and ent["_name"] not in used_entity_names]
            if not ents:
                continue
            branches.append({
                "label": label,
                "category": node.get("category", "fallback_group"),
                "entities": ents,
                "all_entities": ents,
                "branch_entity": None,
                "type_key": "fallback",
                "fallback_display_schema": True,
            })
            if len(branches) >= 4:
                break

    scored = []
    for branch in branches:
        label = branch["label"]
        if label == "代表企业" or branch.get("category") == "company_group":
            debug["rejected_first_level_nodes"].append({
                "label": label,
                "reason": "representative_company_branch_removed",
            })
            continue
        if is_forbidden_tree_label(label):
            debug["rejected_first_level_nodes"].append({
                "label": label,
                "reason": "forbidden_default_first_level_node",
            })
            continue
        title_hits = sum(1 for title in titles if label.replace("主链", "") in title or label in title)
        scoring_entities = branch.get("all_entities", branch["entities"])
        relation_count = sum(len(support["by_entity"].get(ent["_name"], [])) for ent in scoring_entities)
        evidence_ids = set()
        for ent in scoring_entities:
            evidence_ids.update(evidence_for_entity(ent["_name"], ent, support))
        score = (
            title_hits * 2.0
            + len(scoring_entities) * 1.5
            + relation_count * 1.5
            + len(evidence_ids) * 1.0
            + min(len(scoring_entities), 6) * 1.2
        )
        if any(clean_label(root_label).replace("产业链", "") in title for title in titles):
            score += 0.8
        if is_generic_term(label):
            score -= 3.0
        if len(scoring_entities) == 0:
            score -= 2.0
        branch.update({
            "score": round(score, 2),
            "title_hits": title_hits,
            "relation_count": relation_count,
            "evidence_ids": sorted(evidence_ids & evidence_universe),
            "entity_count": len(scoring_entities),
            "selected_reason": "实体/关系/标题证据综合得分靠前",
        })
        scored.append(branch)

    scored.sort(key=lambda b: (-b["score"], b["label"]))
    max_nodes = safe_int(template.get("max_first_level_nodes", 6), 6)
    selected = scored[:max_nodes]
    selected_labels = {b["label"] for b in selected}
    for branch in scored:
        item = {
            "label": branch["label"],
            "score": branch["score"],
            "title_hits": branch["title_hits"],
            "entity_count": branch["entity_count"],
            "relation_count": branch["relation_count"],
            "evidence_count": len(branch["evidence_ids"]),
            "selected": branch["label"] in selected_labels,
            "selected_reason": branch.get("selected_reason", ""),
            "rejected_reason": "" if branch["label"] in selected_labels else "超过max_first_level_nodes或得分较低",
            "fallback_display_schema": branch.get("fallback_display_schema", False),
        }
        debug["candidate_first_level_nodes"].append(item)
    return selected, {
        "maps": maps,
        "support": support,
        "titles": titles,
        "evidence_universe": evidence_universe,
        "debug": debug,
        "validation_rules": template.get("validation_rules", {}),
        "max_depth": safe_int(template.get("max_depth", 4), 4),
    }


def call_ollama_for_suggestions(summary: dict[str, Any], logger: logging.Logger | None = None) -> tuple[dict[str, Any] | None, str, str]:
    prompt = (
        "你是产业链层级树建议器。只能基于输入的已审核实体、关系、章节标题和证据ID，"
        "给出JSON建议，不得使用常识补充无证据节点。JSON字段包括"
        "root_label, first_level_nodes, warnings。不要输出上游/中游/下游作为默认一级节点。\n"
        f"输入摘要:\n{json.dumps(summary, ensure_ascii=False)}"
    )
    payload = {
        "model": "qwen3:8b",
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
    }
    raw = ""
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        raw = data.get("response", "")
        parsed = json.loads(raw) if raw else None
        return parsed, hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else "", raw
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, Exception) as exc:
        if logger:
            logger.warning(f"Ollama模板建议不可用，使用规则法fallback: {exc}")
        return None, "", raw


def add_tree_node(nodes: list[dict[str, Any]], label: str, parent_id: str, depth: int, category: str,
                  sort_order: int, source_from: str, source_entity_ids: list[str],
                  evidence_ids: list[str], is_schema_node: bool, notes: str) -> str:
    tid = f"T{len(nodes)+1:04d}"
    nodes.append({
        "tree_node_id": tid,
        "label": label,
        "display_label": label,
        "parent_id": parent_id,
        "level": depth,
        "depth": depth,
        "sort_order": sort_order,
        "category": category,
        "node_type": "display_node" if is_schema_node else "approved_entity",
        "source_from": source_from,
        "source_entity_ids": ";".join(source_entity_ids),
        "evidence_ids": ";".join(evidence_ids),
        "is_display_node": "true",
        "is_schema_node": "true" if is_schema_node else "false",
        "notes": notes,
    })
    return tid


def add_tree_edge(edges: list[dict[str, Any]], parent_id: str, child_id: str, sort_order: int,
                  evidence_ids: list[str] | None = None, notes: str = "展示层级边，非PDF证据事实",
                  is_evidence_fact: bool = False) -> None:
    edges.append({
        "tree_edge_id": f"TE{len(edges)+1:04d}",
        "parent_tree_node_id": parent_id,
        "child_tree_node_id": child_id,
        "edge_type": "DISPLAY_PARENT_OF",
        "sort_order": sort_order,
        "source_from": "approved_relation" if is_evidence_fact else "document_driven_rules",
        "is_evidence_fact": "true" if is_evidence_fact else "false",
        "evidence_ids": ";".join(evidence_ids or []),
        "notes": notes,
    })


def build_tree_from_approved_relations(inputs: dict[str, Any], context: dict[str, Any], root_label: str,
                                       template: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """以已验证关系为骨架构建树；无关系实体只导出为未分类，不猜测其父节点。"""
    maps = context["maps"]
    evidence_universe = context["evidence_universe"]
    allowed_types = set((template.get("tree_build_rules") or {}).get(
        "parent_child_relation_types", ["PART_OF", "INPUT_TO"]))
    max_depth = safe_int(template.get("max_depth", 4), 4)

    # 每个子实体只保留一个证据最强父实体；PART_OF 优先于输入关系。
    parent_choice: dict[str, tuple[tuple[int, int, str], str, dict[str, Any], list[str]]] = {}
    involved_names = set()
    rejected_relation_names = set()
    for rel in inputs["relations"]:
        rtype = str(rel.get("relation_type", ""))
        if rtype not in allowed_types:
            continue
        child = clean_label(rel.get("subject_canonical_name", ""))
        parent = clean_label(rel.get("object_canonical_name", ""))
        if not child or not parent or child == parent:
            continue
        if is_forbidden_tree_label(child) or is_forbidden_tree_label(parent):
            rejected_relation_names.update({child, parent})
            continue
        if is_generic_term(child) or is_generic_term(parent):
            rejected_relation_names.update({child, parent})
            continue
        child_ent = maps["by_name"].get(child)
        parent_ent = maps["by_name"].get(parent)
        if not child_ent or not parent_ent:
            continue
        if child_ent.get("_type") in EXCLUDED_TREE_ENTITY_TYPES or parent_ent.get("_type") in EXCLUDED_TREE_ENTITY_TYPES:
            continue
        evs = [eid for eid in split_evidence_ids(rel.get("evidence_ids", "")) if eid in evidence_universe]
        if not evs:
            rid = relation_id(rel)
            for ev in inputs["relation_evidence"]:
                ev_rid = ev.get("approved_relation_id") or ev.get("normalized_relation_id", "")
                if ev_rid == rid and ev.get("evidence_id") in evidence_universe:
                    evs.append(ev["evidence_id"])
        evs = sorted(set(evs))
        if not evs:
            continue
        priority = 2 if rtype == "PART_OF" else 1
        score = (priority, len(evs), relation_id(rel))
        current = parent_choice.get(child)
        if current is None or score > current[0]:
            parent_choice[child] = (score, parent, rel, evs)
        involved_names.update({child, parent})

    # 阻断简单环：若沿父指针可回到当前子节点，则丢弃该边。
    for child in list(parent_choice):
        seen = {child}
        cursor = parent_choice[child][1]
        cyclic = False
        while cursor in parent_choice:
            if cursor in seen:
                cyclic = True
                break
            seen.add(cursor)
            cursor = parent_choice[cursor][1]
        if cyclic:
            parent_choice.pop(child, None)

    children_by_parent: dict[str, list[tuple[str, dict[str, Any], list[str]]]] = defaultdict(list)
    for child, (_, parent, rel, evs) in parent_choice.items():
        children_by_parent[parent].append((child, rel, evs))
    graph_names = set(parent_choice)
    graph_names.update(parent for _, parent, _, _ in parent_choice.values())
    all_children = set(parent_choice)
    roots = sorted(name for name in graph_names if name not in all_children and name in maps["by_name"])

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    used_entity_ids = set()
    used_names = set()
    root_id = add_tree_node(nodes, root_label, "", 0, "root", 0,
                            "document_driven_rules", [], [], True, "单一根节点")

    def add_subtree(name: str, parent_tid: str, depth: int, sort_order: int,
                    supporting_rel: dict[str, Any] | None = None,
                    supporting_evs: list[str] | None = None) -> None:
        if depth > max_depth or name in used_names:
            return
        ent = maps["by_name"][name]
        eid = ent.get("_id", "")
        own_evs = sorted(set(supporting_evs or []))
        tid = add_tree_node(
            nodes, name, parent_tid, depth, ent.get("_type", ""), sort_order,
            "approved_relation" if supporting_rel else "approved_entity",
            [eid] if eid else [], own_evs, False,
            (f"approved_relation:{relation_id(supporting_rel)}" if supporting_rel else
             "relation_connected_root_branch"),
        )
        add_tree_edge(
            edges, parent_tid, tid, sort_order, own_evs,
            (f"由approved关系{relation_id(supporting_rel)}确定父子层级" if supporting_rel else
             "关系子树根挂载到产业链总根"),
            is_evidence_fact=bool(supporting_rel),
        )
        used_names.add(name)
        if eid:
            used_entity_ids.add(eid)
        for idx, (child, rel, evs) in enumerate(sorted(children_by_parent.get(name, []), key=lambda x: x[0]), 1):
            add_subtree(child, tid, depth + 1, idx, rel, evs)

    for idx, name in enumerate(roots, 1):
        add_subtree(name, root_id, 1, idx)

    unclassified = []
    for ent in maps["entities"]:
        if ent.get("_id") == "CE_ROOT" or ent.get("_id") in used_entity_ids:
            continue
        name = ent.get("_name", "")
        if ent.get("_type") in EXCLUDED_TREE_ENTITY_TYPES:
            reason = "company实体按配置排除，不进入最终树状结构"
        elif name in rejected_relation_names or is_forbidden_tree_label(name) or is_generic_term(name):
            reason = "泛化/禁用节点不进入树；仅保留具体产业链内容"
        elif name in graph_names:
            reason = "关系链超过最大深度或存在环，未进入树"
        else:
            reason = "缺少已验证父子/输入关系，避免猜测挂载位置"
        unclassified.append({
            "entity_id": ent.get("_id", ""), "canonical_name": name,
            "entity_type": ent.get("_type", ""), "entity_level": ent.get("entity_level", ""),
            "value_chain_stage": ent.get("value_chain_stage", ""), "reason": reason,
            "recommended_template_node": "补充明确关系证据后再进入树",
        })

    selected = []
    node_by_name = {n["label"]: n for n in nodes}
    for name in roots:
        descendants = len(children_by_parent.get(name, []))
        root_evs = sorted({eid for _, _, evs in children_by_parent.get(name, []) for eid in evs})
        selected.append({
            "label": name, "category": node_by_name.get(name, {}).get("category", ""),
            "score": descendants + len(root_evs), "entity_count": descendants + 1,
            "relation_count": descendants, "evidence_ids": root_evs,
            "fallback_display_schema": False,
        })
    return nodes, edges, unclassified, selected


def build_tree_from_branches(selected: list[dict[str, Any]], context: dict[str, Any], root_label: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    support = context["support"]
    evidence_universe = context["evidence_universe"]
    used_entity_ids = set()
    used_labels = set()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    root_id = add_tree_node(nodes, root_label, "", 0, "root", 0, "document_driven_rules", [], [], True, "单一根节点")
    used_labels.add(root_label)

    for bidx, branch in enumerate(selected, start=1):
        branch_evidence = [eid for eid in branch.get("evidence_ids", []) if eid in evidence_universe]
        branch_entity = branch.get("branch_entity") or {}
        branch_entity_id = branch_entity.get("_id", "")
        bid = add_tree_node(
            nodes, branch["label"], root_id, 1, branch["category"], bidx,
            "approved_entity" if branch_entity_id else "document_driven_rules",
            [branch_entity_id] if branch_entity_id else [], branch_evidence,
            not bool(branch_entity_id),
            ("approved_entity_promoted_to_branch; " if branch_entity_id else "display_schema_node; ")
            + ("fallback_display_schema" if branch.get("fallback_display_schema") else branch["selected_reason"]),
        )
        add_tree_edge(edges, root_id, bid, bidx)
        used_labels.add(branch["label"])
        if branch_entity_id:
            used_entity_ids.add(branch_entity_id)

        entities = sorted(
            branch["entities"],
            key=lambda e: (-e.get("_relation_degree", 0), e.get("_type", ""), e["_name"]),
        )
        child_sort = 0
        for ent in entities:
            name = ent["_name"]
            eid = ent["_id"]
            if ent.get("_type") in EXCLUDED_TREE_ENTITY_TYPES:
                continue
            if eid in used_entity_ids or name in used_labels or ent.get("_id") == "CE_ROOT":
                continue
            if branch["type_key"] == "core" and ent.get("_type") in {"material", "equipment", "technology", "company"}:
                continue
            if branch["type_key"] != "core" and ent.get("_type") in {"industry", "industry_link", "process"}:
                continue
            evs = [i for i in evidence_for_entity(name, ent, support) if i in evidence_universe]
            child_sort += 1
            cid = add_tree_node(
                nodes, name, bid, 2, ent.get("_type", ""), child_sort,
                "approved_entity", [eid], evs, False,
                f"approved_entity; entity_type={ent.get('_type','')}; value_chain_stage={ent.get('value_chain_stage','')}",
            )
            add_tree_edge(edges, bid, cid, child_sort, evs)
            used_entity_ids.add(eid)
            used_labels.add(name)

            if branch["type_key"] == "core":
                rel_children = []
                for rel in support["by_parent_object"].get(name, []):
                    subj_name = clean_label(rel.get("subject_canonical_name", ""))
                    subj_type = rel.get("subject_type", "")
                    if subj_type not in {"process", "industry_link"} or subj_name in used_labels:
                        continue
                    rel_children.append((subj_name, rel))
                for gsort, (subj_name, rel) in enumerate(rel_children[:6], start=1):
                    ent_row = context["maps"]["by_name"].get(subj_name, {})
                    subj_id = ent_row.get("_id", "")
                    evs2 = [i for i in rel.get("_evidence_ids_list", []) if i in evidence_universe]
                    gid = add_tree_node(
                        nodes, subj_name, cid, 3, ent_row.get("_type", rel.get("subject_type", "")), gsort,
                        "approved_entity", [subj_id] if subj_id else [], evs2, False,
                        "approved_relation_supported_child",
                    )
                    add_tree_edge(edges, cid, gid, gsort, evs2)
                    if subj_id:
                        used_entity_ids.add(subj_id)
                    used_labels.add(subj_name)

    unclassified = []
    for ent in context["maps"]["entities"]:
        if ent["_id"] == "CE_ROOT":
            continue
        if ent.get("_type") in EXCLUDED_TREE_ENTITY_TYPES:
            unclassified.append({
                "entity_id": ent["_id"],
                "canonical_name": ent["_name"],
                "entity_type": ent.get("_type", ""),
                "entity_level": ent.get("entity_level", ""),
                "value_chain_stage": ent.get("value_chain_stage", ""),
                "reason": "company实体按配置排除，不进入最终树状结构",
                "recommended_template_node": "不建模",
            })
            continue
        if ent["_id"] not in used_entity_ids:
            unclassified.append({
                "entity_id": ent["_id"],
                "canonical_name": ent["_name"],
                "entity_type": ent.get("_type", ""),
                "entity_level": ent.get("entity_level", ""),
                "value_chain_stage": ent.get("value_chain_stage", ""),
                "reason": "未进入得分最高的动态树或为重复/弱支撑节点",
                "recommended_template_node": "可在dynamic_template_debug.json中查看候选分支后调整",
            })
    return nodes, edges, unclassified


def validate_dynamic_tree(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    entity_ids = set(context["maps"]["by_id"].keys())
    evidence_ids = context["evidence_universe"]
    labels_l1 = [n["label"] for n in nodes if safe_int(n.get("depth")) == 1]
    node_ids = {n["tree_node_id"] for n in nodes}
    parent_count = Counter(e["child_tree_node_id"] for e in edges)
    issues = []
    if sum(1 for n in nodes if n.get("parent_id") == "") != 1:
        issues.append("root_count_not_one")
    all_labels = {n.get("label", "") for n in nodes}
    bad_labels = {label for label in all_labels if is_forbidden_tree_label(label)}
    if bad_labels:
        issues.append(f"forbidden_tree_nodes:{sorted(bad_labels)}")
    if "代表企业" in {n.get("label", "") for n in nodes}:
        issues.append("representative_company_node_present")
    if any(n.get("category") == "company_group" for n in nodes):
        issues.append("company_group_node_present")
    source_entity_owner = {}
    for n in nodes:
        if n.get("category") == "company" or n.get("node_type") == "company":
            issues.append(f"company_node_present:{n.get('label','')}")
        for sid in split_evidence_ids(n.get("source_entity_ids", "")):
            if sid and sid not in entity_ids:
                issues.append(f"unknown_source_entity_id:{sid}")
            if sid and sid in source_entity_owner and source_entity_owner[sid] != n.get("tree_node_id"):
                issues.append(f"duplicate_source_entity_id:{sid}")
            elif sid:
                source_entity_owner[sid] = n.get("tree_node_id")
        for eid in split_evidence_ids(n.get("evidence_ids", "")):
            if eid and eid not in evidence_ids:
                issues.append(f"unknown_evidence_id:{eid}")
    for e in edges:
        if e["parent_tree_node_id"] not in node_ids or e["child_tree_node_id"] not in node_ids:
            issues.append(f"edge_endpoint_missing:{e['tree_edge_id']}")
        if parent_count[e["child_tree_node_id"]] > 1:
            issues.append(f"duplicate_parent:{e['child_tree_node_id']}")
        if e.get("is_evidence_fact") == "true":
            for eid in split_evidence_ids(e.get("evidence_ids", "")):
                if eid not in evidence_ids:
                    issues.append(f"evidence_fact_unknown_evidence:{eid}")
    max_depth = max((safe_int(n.get("depth")) for n in nodes), default=0)
    rules = context.get("validation_rules", {})
    preferred_min = safe_int(rules.get("preferred_depth_min", 2), 2)
    allowed_max = safe_int(context.get("max_depth", 4), 4)
    if max_depth < preferred_min:
        issues.append("tree_depth_below_minimum")
    if max_depth > allowed_max:
        issues.append("tree_depth_above_maximum")
    return {
        "passed": len(issues) == 0,
        "issues": sorted(set(issues)),
        "first_level_labels": labels_l1,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "max_depth": max_depth,
        "min_allowed_depth": preferred_min,
        "max_allowed_depth": allowed_max,
    }


def write_quality_report(path: Path, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    children = defaultdict(list)
    for e in edges:
        children[e["parent_tree_node_id"]].append(e["child_tree_node_id"])
    rows = []
    for n in nodes:
        if safe_int(n.get("depth")) == 0:
            continue
        child_count = len(children.get(n["tree_node_id"], []))
        evidence_count = len(split_evidence_ids(n.get("evidence_ids", "")))
        entity_count = len(split_evidence_ids(n.get("source_entity_ids", "")))
        relation_count = 0
        issue = ""
        action = "保持"
        status = "ok"
        if safe_int(n.get("depth")) == 1 and child_count == 0:
            status = "weak_branch"
            issue = "一级节点缺少子节点"
            action = "增加更多已审核实体或调整分支阈值"
        elif n.get("is_schema_node") == "true" and evidence_count == 0:
            status = "schema_without_direct_evidence"
            issue = "展示分类节点无直接证据，依赖子节点支撑"
            action = "确认该分类是否适合作为展示结构"
        rows.append({
            "branch_label": n["label"],
            "depth": n["depth"],
            "child_count": child_count,
            "evidence_count": evidence_count,
            "entity_count": entity_count,
            "relation_count": relation_count,
            "is_display_schema_node": n.get("is_schema_node", "false"),
            "quality_status": status,
            "issue": issue,
            "recommended_action": action,
        })
    write_csv(path, rows, [
        "branch_label", "depth", "child_count", "evidence_count", "entity_count",
        "relation_count", "is_display_schema_node", "quality_status", "issue", "recommended_action",
    ])


def write_reports(out_dir: Path, root_label: str, template: dict[str, Any], selected: list[dict[str, Any]],
                  nodes: list[dict[str, Any]], edges: list[dict[str, Any]], unclassified: list[dict[str, Any]],
                  validation: dict[str, Any], llm_hash: str, llm_used: bool) -> None:
    first_level = [n for n in nodes if safe_int(n.get("depth")) == 1]
    first_level_lines = []
    for branch in selected:
        first_level_lines.append(
            f"- {branch['label']}: score={branch.get('score')}, entity_count={branch.get('entity_count')}, "
            f"relation_count={branch.get('relation_count')}, evidence_count={len(branch.get('evidence_ids', []))}"
        )
    entity_nodes = [n for n in nodes if n.get("is_schema_node") == "false"]
    schema_nodes = [n for n in nodes if n.get("is_schema_node") == "true"]
    fallback = any(b.get("fallback_display_schema") for b in selected)
    report = [
        "# 模板应用报告",
        "",
        "- 使用模板: document_driven",
        f"- 模板名称: {template.get('template_name', '')}",
        f"- root_label: {root_label}",
        f"- 一级节点生成方式: 规则评分 + 可选本地Ollama建议；最终结果经程序校验",
        f"- 本地LLM建议: {'已尝试并记录hash' if llm_used else '未使用或不可用，已fallback到规则法'}",
        f"- LLM原始建议hash: {llm_hash or 'N/A'}",
        f"- 是否发生fallback_display_schema: {'是' if fallback else '否'}",
        f"- 未分类实体: {len(unclassified)}",
        f"- 程序校验: {'通过' if validation.get('passed') else '未通过'}",
        "",
        "## 一级节点及证据支持",
        *first_level_lines,
        "",
        "## 节点来源",
        f"- PDF抽取实体节点: {len(entity_nodes)}",
        f"- display_schema_node: {len(schema_nodes)}",
        "",
        "## 为什么没有默认使用“上游/中游/下游”",
        "document_driven 模板禁止将“上游/中游/下游”作为默认一级节点；本次一级节点来自已审核实体类型、关系连接、章节标题与证据命中后的综合评分。",
        "",
        "## 结构不理想时如何调整",
        "优先检查 approved_entities.csv、approved_relations.csv 和章节标题质量；必要时提高实体审核质量，或在 document_driven.yaml 中调整 max_first_level_nodes 与评分权重。",
    ]
    (out_dir / "template_application_report.md").write_text("\n".join(report), encoding="utf-8")


def build_document_driven_tree(project_root: Path, template: dict[str, Any], root_label: str, out_dir: Path,
                               logger: logging.Logger | None = None, allow_llm: bool = True) -> dict[str, Any]:
    logger = logger or logging.getLogger(__name__)
    inputs = load_inputs(project_root)
    selected, context = make_branch_candidates(inputs, template, root_label)

    summary = {
        "root_label": root_label,
        "entities": [
            {"id": entity_id(e), "name": entity_name(e), "type": e.get("entity_type", ""), "stage": e.get("value_chain_stage", "")}
            for e in inputs["entities"][:80]
            if e.get("entity_type", "") not in EXCLUDED_TREE_ENTITY_TYPES
        ],
        "relations": [
            {"id": relation_id(r), "subject": r.get("subject_canonical_name", ""), "type": r.get("relation_type", ""), "object": r.get("object_canonical_name", ""), "evidence_ids": r.get("evidence_ids", "")}
            for r in inputs["relations"][:80]
        ],
        "titles": context["titles"][:40],
    }
    llm_suggestion = None
    llm_hash = ""
    llm_raw = ""
    relation_first = bool((template.get("tree_build_rules") or {}).get("relation_first", True)) and bool(inputs["relations"])
    if allow_llm and not relation_first and template.get("first_level_generation_method") == "llm_plus_rules":
        llm_suggestion, llm_hash, llm_raw = call_ollama_for_suggestions(summary, logger)

    if relation_first:
        nodes, edges, unclassified, selected = build_tree_from_approved_relations(
            inputs, context, root_label, template)
    else:
        nodes, edges, unclassified = build_tree_from_branches(selected, context, root_label)
    validation = validate_dynamic_tree(nodes, edges, context)

    debug = context["debug"]
    debug.update({
        "llm_suggestion_hash": llm_hash,
        "llm_suggestion_used_directly": False,
        "llm_suggestion_available": llm_suggestion is not None,
        "relation_first_tree_used": relation_first,
        "llm_raw_suggestion": llm_raw[:4000] if llm_raw else "",
        "program_validation": validation,
        "source_stage_dir": str(inputs["stage_source_dir"]),
        "source_parsed_dir": str(inputs["parsed_dir"]) if inputs["parsed_dir"] else "",
    })
    (out_dir / "dynamic_template_debug.json").write_text(json.dumps(debug, ensure_ascii=False, indent=2), encoding="utf-8")
    write_quality_report(out_dir / "template_quality_report.csv", nodes, edges)
    write_csv(out_dir / "unclassified_entities.csv", unclassified, [
        "entity_id", "canonical_name", "entity_type", "entity_level",
        "value_chain_stage", "reason", "recommended_template_node",
    ])
    write_reports(out_dir, root_label, template, selected, nodes, edges, unclassified, validation, llm_hash, llm_suggestion is not None)

    return {
        "nodes": nodes,
        "edges": edges,
        "unclassified": unclassified,
        "validation": validation,
        "selected_first_level_nodes": [b["label"] for b in selected],
        "llm_suggestion_hash": llm_hash,
        "llm_suggestion_available": llm_suggestion is not None,
        "fallback_display_schema": any(b.get("fallback_display_schema") for b in selected),
        "recommended_next_stage": "hierarchy_refinement",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="构建document_driven动态产业链树")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--root-label", default="产业链")
    parser.add_argument("--template", default=None)
    parser.add_argument("--allow-llm-template-generation", action="store_true", default=False)
    args = parser.parse_args()

    project_root = Path(args.project_root)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    template_path = Path(args.template) if args.template else project_root / "rag" / "templates" / "document_driven.yaml"
    template = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    result = build_document_driven_tree(
        project_root, template, args.root_label, out_dir,
        logging.getLogger(__name__), allow_llm=args.allow_llm_template_generation,
    )
    print(json.dumps({
        "node_count": len(result["nodes"]),
        "edge_count": len(result["edges"]),
        "first_level_nodes": result["selected_first_level_nodes"],
        "validation": result["validation"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
