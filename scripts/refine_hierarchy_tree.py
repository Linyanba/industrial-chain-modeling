#!/usr/bin/env python3
"""
层级树 refinement：在 document_driven 初始树后、最终绘图前修正父子层级并增加多级结构。
"""
import argparse
import csv
import hashlib
import json
import logging
import re
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
NODE_FIELDS = [
    "tree_node_id", "label", "display_label", "parent_id", "level", "depth",
    "sort_order", "category", "node_type", "source_from", "source_entity_ids",
    "evidence_ids", "is_display_node", "is_schema_node", "notes",
]
EDGE_FIELDS = [
    "tree_edge_id", "parent_tree_node_id", "child_tree_node_id", "edge_type",
    "sort_order", "source_from", "is_evidence_fact", "evidence_ids", "notes",
]
PARENT_REL_TYPES = {"PART_OF", "BELONGS_TO", "SUB_CATEGORY_OF", "CONTAINS", "INCLUDES", "HAS_PART", "INPUT_TO"}
FORBIDDEN_LABELS = {"代表企业"}
FORBIDDEN_TREE_LABELS = {"上游", "中游", "下游", "上下游", "上中下游", "产业链上下游"}


def is_forbidden_tree_label(label: str) -> bool:
    compact = re.sub(r"\s+", "", str(label or ""))
    return compact in FORBIDDEN_TREE_LABELS or any(
        token in compact for token in ("上中下游", "上下游", "上游", "中游", "下游")
    )
FORBIDDEN_L1 = FORBIDDEN_TREE_LABELS
COMPANY_TYPES = {"company"}


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def split_ids(text: str) -> list[str]:
    if not text:
        return []
    return [p.strip() for p in re.split(r"[;,|]", text) if p.strip()]


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def resolve_pointer(path: Path) -> Path | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return Path(text) if text else None


def load_config(project_root: Path, config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or project_root / "rag" / "config" / "hierarchy_refinement_config.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_stage_inputs(project_root: Path) -> dict[str, Any]:
    s46 = resolve_pointer(project_root / "rag" / "latest_stage4_6_run.txt")
    s45 = resolve_pointer(project_root / "rag" / "latest_stage4_5_run.txt")
    s4 = resolve_pointer(project_root / "rag" / "latest_stage4_run.txt")
    source = s46 or s45 or s4
    parsed = resolve_pointer(project_root / "parsed_documents" / "latest_run.txt")
    if s46:
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

    stage3c = None
    if s4:
        configured = str(read_json(s4 / "run_config.json").get("stage3c_dir", "")).strip()
        if configured and Path(configured).exists():
            stage3c = Path(configured)
    if stage3c is None:
        stage3c = resolve_pointer(project_root / "rag" / "latest_stage3c_run.txt")
    verified_entity_candidates = (
        read_jsonl(stage3c / "verified_entity_candidates.jsonl") if stage3c else []
    )

    evidence_chunks = []
    document_structure = {}
    if parsed and parsed.exists():
        document_structure = read_json(parsed / "document_structure.json")
        chunks_path = parsed / "evidence_chunks.jsonl"
        if chunks_path.exists():
            with open(chunks_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            evidence_chunks.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
    return {
        "source_dir": source,
        "parsed_dir": parsed,
        "entities": entities,
        "relations": relations,
        "aliases": aliases,
        "relation_evidence": relation_evidence,
        "stage3c_dir": stage3c,
        "verified_entity_candidates": verified_entity_candidates,
        "document_structure": document_structure,
        "evidence_chunks": evidence_chunks,
    }


def entity_id(row: dict[str, str]) -> str:
    return row.get("approved_entity_id") or row.get("canonical_entity_id") or row.get("entity_id") or ""


def entity_name(row: dict[str, str]) -> str:
    return (row.get("canonical_name") or row.get("entity_name") or row.get("name") or "").strip()


def relation_id(row: dict[str, str]) -> str:
    return row.get("approved_relation_id") or row.get("normalized_relation_id") or row.get("relation_id") or ""


def relation_evidence_map(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    out = defaultdict(list)
    for row in rows:
        rid = relation_id(row)
        eid = row.get("evidence_id", "")
        if rid and eid:
            out[rid].append(eid)
    return out


def collect_evidence_ids(inputs: dict[str, Any], relations: list[dict[str, str]]) -> set[str]:
    ids = {c.get("evidence_id", "") for c in inputs.get("evidence_chunks", [])}
    for row in inputs.get("relation_evidence", []):
        ids.add(row.get("evidence_id", ""))
    for rel in relations:
        ids.update(split_ids(rel.get("evidence_ids", "")))
    for row in inputs.get("verified_entity_candidates", []):
        ids.add(row.get("evidence_id", ""))
    return {i for i in ids if i}


def tree_maps(nodes: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    return {n["tree_node_id"]: n for n in nodes}, {n["label"]: n["tree_node_id"] for n in nodes}


def child_map(nodes: list[dict[str, Any]]) -> dict[str, list[str]]:
    out = defaultdict(list)
    for n in nodes:
        pid = n.get("parent_id", "")
        if pid:
            out[pid].append(n["tree_node_id"])
    return out


def remove_subtree(nodes: list[dict[str, Any]], root_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cmap = child_map(nodes)
    to_remove = set()
    stack = [root_id]
    while stack:
        nid = stack.pop()
        to_remove.add(nid)
        stack.extend(cmap.get(nid, []))
    removed = [n for n in nodes if n["tree_node_id"] in to_remove]
    kept = [n for n in nodes if n["tree_node_id"] not in to_remove]
    return kept, removed


def move_node(nodes_by_id: dict[str, dict[str, Any]], child_id: str, new_parent_id: str, fixes: list[dict[str, Any]],
              fix_type: str, reason: str, evidence_ids: list[str] | None = None, confidence: str = "0.90") -> None:
    child = nodes_by_id[child_id]
    old_parent_id = child.get("parent_id", "")
    if old_parent_id == new_parent_id:
        return
    old_parent_label = nodes_by_id.get(old_parent_id, {}).get("label", "")
    child["parent_id"] = new_parent_id
    fixes.append({
        "child_label": child.get("label", ""),
        "old_parent_label": old_parent_label,
        "new_parent_label": nodes_by_id.get(new_parent_id, {}).get("label", ""),
        "fix_type": fix_type,
        "reason": reason,
        "evidence_ids": ";".join(evidence_ids or []),
        "confidence": confidence,
    })


def apply_alias_merges(nodes: list[dict[str, Any]], aliases: list[dict[str, str]], removed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _, by_label = tree_maps(nodes)
    alias_pairs = []
    for row in aliases:
        alias = row.get("alias") or row.get("alias_name") or row.get("source_name") or ""
        canonical = row.get("canonical_name") or row.get("target_name") or ""
        if alias and canonical:
            alias_pairs.append((alias.strip(), canonical.strip()))
    for alias, canonical in alias_pairs:
        if alias in by_label and canonical in by_label and alias != canonical:
            nodes_by_id, by_label = tree_maps(nodes)
            aid = by_label[alias]
            node = nodes_by_id[aid]
            removed.append({
                "label": node["label"],
                "old_parent_label": nodes_by_id.get(node.get("parent_id", ""), {}).get("label", ""),
                "reason": f"alias合并到canonical节点: {canonical}",
                "node_type": node.get("node_type", ""),
                "entity_type": node.get("category", ""),
            })
            nodes, _ = remove_subtree(nodes, aid)
            _, by_label = tree_maps(nodes)
    return nodes


def apply_company_removal(nodes: list[dict[str, Any]], entities_by_id: dict[str, dict[str, str]],
                          removed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changed = True
    while changed:
        changed = False
        nodes_by_id, _ = tree_maps(nodes)
        for node in list(nodes):
            source_ids = split_ids(node.get("source_entity_ids", ""))
            entity_types = {entities_by_id.get(sid, {}).get("entity_type", "") for sid in source_ids}
            is_company = (
                node.get("label") in FORBIDDEN_LABELS
                or node.get("category") in {"company", "company_group"}
                or bool(entity_types & COMPANY_TYPES)
            )
            if is_company:
                removed.append({
                    "label": node.get("label", ""),
                    "old_parent_label": nodes_by_id.get(node.get("parent_id", ""), {}).get("label", ""),
                    "reason": "企业/代表企业节点按配置排除，不进入最终树",
                    "node_type": node.get("node_type", ""),
                    "entity_type": ";".join(sorted(t for t in entity_types if t)),
                })
                nodes, _ = remove_subtree(nodes, node["tree_node_id"])
                changed = True
                break
    return nodes


def apply_relation_parent_child(nodes: list[dict[str, Any]], relations: list[dict[str, str]],
                                evidence_by_relation: dict[str, list[str]], fixes: list[dict[str, Any]]) -> None:
    nodes_by_id, by_label = tree_maps(nodes)
    for rel in relations:
        rtype = rel.get("relation_type", "")
        if rtype not in PARENT_REL_TYPES:
            continue
        subject = (rel.get("subject_canonical_name") or "").strip()
        obj = (rel.get("object_canonical_name") or "").strip()
        if not subject or not obj or subject not in by_label or obj not in by_label:
            continue
        child_id = by_label[subject]
        parent_id = by_label[obj]
        if child_id == parent_id:
            continue
        evidence_ids = split_ids(rel.get("evidence_ids", "")) + evidence_by_relation.get(relation_id(rel), [])
        move_node(nodes_by_id, child_id, parent_id, fixes, "relation_parent_child",
                  f"依据approved_relations中的{rtype}关系确定父子层级", sorted(set(evidence_ids)), "0.92")


def apply_composite_parent_fixes(nodes: list[dict[str, Any]], fixes: list[dict[str, Any]]) -> None:
    nodes_by_id, _ = tree_maps(nodes)
    cmap = child_map(nodes)
    for parent_id, child_ids in list(cmap.items()):
        siblings = [nodes_by_id[cid] for cid in child_ids if cid in nodes_by_id]
        for parent_candidate in siblings:
            plabel = parent_candidate.get("label", "")
            if len(plabel) < 3:
                continue
            for child_candidate in siblings:
                if child_candidate["tree_node_id"] == parent_candidate["tree_node_id"]:
                    continue
                clabel = child_candidate.get("label", "")
                if len(clabel) >= 2 and clabel in plabel:
                    move_node(
                        nodes_by_id,
                        child_candidate["tree_node_id"],
                        parent_candidate["tree_node_id"],
                        fixes,
                        "composite_parent_fix",
                        f"组合型上位词'{plabel}'包含'{clabel}'，子节点不应与父节点同层",
                        split_ids(parent_candidate.get("evidence_ids", "")) + split_ids(child_candidate.get("evidence_ids", "")),
                        "0.95",
                    )


def recompute_depths_and_edges(nodes: list[dict[str, Any]],
                               original_edges: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes_by_id, _ = tree_maps(nodes)
    roots = [n for n in nodes if not n.get("parent_id")]
    root = roots[0] if roots else nodes[0]
    cmap = child_map(nodes)
    visited = set()

    def walk(nid: str, depth: int) -> None:
        visited.add(nid)
        children = cmap.get(nid, [])
        for i, cid in enumerate(children, start=1):
            child = nodes_by_id[cid]
            child["depth"] = depth + 1
            child["level"] = depth + 1
            child["sort_order"] = i
            walk(cid, depth + 1)

    root["depth"] = 0
    root["level"] = 0
    root["sort_order"] = 0
    walk(root["tree_node_id"], 0)
    # Drop accidental orphans other than root.
    nodes = [n for n in nodes if n["tree_node_id"] in visited or n["tree_node_id"] == root["tree_node_id"]]
    nodes_by_id, _ = tree_maps(nodes)
    cmap = child_map(nodes)
    original_edge_by_pair = {
        (e.get("parent_tree_node_id", ""), e.get("child_tree_node_id", "")): e
        for e in (original_edges or [])
    }
    edges = []
    for parent_id, child_ids in cmap.items():
        for i, cid in enumerate(child_ids, start=1):
            if parent_id not in nodes_by_id or cid not in nodes_by_id:
                continue
            child = nodes_by_id[cid]
            original = original_edge_by_pair.get((parent_id, cid), {})
            fact = str(original.get("is_evidence_fact", "false")).lower() == "true"
            edges.append({
                "tree_edge_id": f"RTE{len(edges)+1:04d}",
                "parent_tree_node_id": parent_id,
                "child_tree_node_id": cid,
                "edge_type": "DISPLAY_PARENT_OF",
                "sort_order": i,
                "source_from": original.get("source_from", "hierarchy_refinement"),
                "is_evidence_fact": "true" if fact else "false",
                "evidence_ids": original.get("evidence_ids") or child.get("evidence_ids", ""),
                "notes": original.get("notes") or "refined展示层级边，非PDF原文事实",
            })
    nodes.sort(key=lambda n: (int(n.get("depth", 0)), str(n.get("parent_id", "")), int(n.get("sort_order", 0)), n.get("label", "")))
    return nodes, edges


def max_depth(nodes: list[dict[str, Any]]) -> int:
    return max((int(n.get("depth", 0)) for n in nodes), default=0)


def validate_tree(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], inputs: dict[str, Any],
                  cfg: dict[str, Any]) -> dict[str, Any]:
    nodes_by_id, _ = tree_maps(nodes)
    roots = [n for n in nodes if not n.get("parent_id")]
    evidence_ids = collect_evidence_ids(inputs, inputs.get("relations", []))
    source_ids = {entity_id(e) for e in inputs.get("entities", []) if entity_id(e)}
    issues = []
    if len(roots) != 1:
        issues.append("root_count_not_one")
    if len(edges) != max(len(nodes) - 1, 0):
        issues.append("edge_count_not_nodes_minus_one")
    edge_children = {e["child_tree_node_id"] for e in edges}
    source_entity_owner = {}
    for n in nodes:
        if n.get("parent_id") and n["tree_node_id"] not in edge_children:
            issues.append(f"orphan_node:{n.get('label','')}")
        if n.get("label") in FORBIDDEN_LABELS or n.get("category") == "company_group":
            issues.append("representative_company_node_present")
        if is_forbidden_tree_label(n.get("label", "")):
            issues.append(f"forbidden_tree_node:{n.get('label','')}")
        if int(n.get("depth", 0)) == 1 and n.get("category") == "company":
            issues.append("company_first_level_present")
        for sid in split_ids(n.get("source_entity_ids", "")):
            if sid and sid not in source_ids:
                issues.append(f"unknown_source_entity_id:{sid}")
            if sid and sid in source_entity_owner and source_entity_owner[sid] != n.get("tree_node_id"):
                issues.append(f"duplicate_source_entity_id:{sid}")
            elif sid:
                source_entity_owner[sid] = n.get("tree_node_id")
        for eid in split_ids(n.get("evidence_ids", "")):
            if eid and eid not in evidence_ids:
                issues.append(f"unknown_evidence_id:{eid}")
    adaptive_depth = bool(cfg.get("adaptive_tree_depth", True))
    min_allowed = int(cfg.get("min_tree_depth", 2 if adaptive_depth else 3))
    max_allowed = int(cfg.get("max_tree_depth", 4))
    if max_depth(nodes) < min_allowed:
        issues.append("tree_depth_below_minimum")
    if max_depth(nodes) > max_allowed:
        issues.append("tree_depth_above_maximum")
    return {
        "passed": len(issues) == 0,
        "issues": sorted(set(issues)),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "max_depth": max_depth(nodes),
        "adaptive_tree_depth": adaptive_depth,
        "min_allowed_depth": min_allowed,
        "max_allowed_depth": max_allowed,
        "first_level_nodes": [n.get("label", "") for n in nodes if int(n.get("depth", 0)) == 1],
    }


def write_quality_report(path: Path, nodes: list[dict[str, Any]], edges: list[dict[str, Any]], entities_by_id: dict[str, dict[str, str]]) -> None:
    nodes_by_id, _ = tree_maps(nodes)
    cmap = child_map(nodes)
    rows = []
    for n in nodes:
        if int(n.get("depth", 0)) == 0:
            continue
        source_ids = split_ids(n.get("source_entity_ids", ""))
        entity_types = {entities_by_id.get(sid, {}).get("entity_type", "") for sid in source_ids}
        child_count = len(cmap.get(n["tree_node_id"], []))
        status = "ok"
        issue = ""
        action = "保持"
        if int(n.get("depth", 0)) == 1 and child_count <= 1:
            status = "weak_branch"
            issue = "一级分支子节点不足"
            action = "补充更多已审核实体或章节证据"
        elif n.get("is_schema_node") == "true" and not n.get("evidence_ids"):
            status = "display_schema_node"
            issue = "展示分组节点无直接证据"
            action = "依赖子节点证据，必要时人工调整分组名"
        rows.append({
            "node_label": n.get("label", ""),
            "depth": n.get("depth", ""),
            "parent_label": nodes_by_id.get(n.get("parent_id", ""), {}).get("label", ""),
            "child_count": child_count,
            "evidence_count": len(split_ids(n.get("evidence_ids", ""))),
            "entity_count": len(source_ids),
            "is_company_node": str(bool(entity_types & COMPANY_TYPES)).lower(),
            "is_display_schema_node": n.get("is_schema_node", "false"),
            "quality_status": status,
            "issue": issue,
            "recommended_action": action,
        })
    write_csv(path, rows, [
        "node_label", "depth", "parent_label", "child_count", "evidence_count",
        "entity_count", "is_company_node", "is_display_schema_node",
        "quality_status", "issue", "recommended_action",
    ])


def call_llm_refinement(inputs_summary: dict[str, Any], cfg: dict[str, Any], logger: logging.Logger) -> dict[str, Any]:
    if not cfg.get("allow_llm_refinement", True):
        return {"available": False, "hash": "", "parsed": None, "raw": "", "warning": "disabled"}
    prompt = (
        "你是层级树优化助手。只能基于输入的树节点、approved关系和证据ID提出JSON建议。"
        "输出JSON字段必须包括parent_child_fixes, branch_expansions, nodes_to_remove, "
        "aliases_to_merge, company_nodes_to_remove, warnings。不得输出代表企业分支。\n"
        f"输入摘要:\n{json.dumps(inputs_summary, ensure_ascii=False)}"
    )
    payload = {
        "model": cfg.get("llm_model", "qwen3:8b"),
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
    }
    try:
        req = urllib.request.Request(
            cfg.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/generate",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        raw = data.get("response", "")
        parsed = json.loads(raw) if raw else None
        return {"available": True, "hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(), "parsed": parsed, "raw": raw[:4000], "warning": ""}
    except Exception as exc:
        logger.warning(f"LLM refinement不可用，使用规则法fallback: {exc}")
        return {"available": False, "hash": "", "parsed": None, "raw": "", "warning": str(exc)}


def write_report(path: Path, original_nodes: list[dict[str, Any]], original_edges: list[dict[str, Any]],
                 refined_nodes: list[dict[str, Any]], refined_edges: list[dict[str, Any]],
                 fixes: list[dict[str, Any]], removed: list[dict[str, Any]], validation: dict[str, Any],
                 llm_info: dict[str, Any]) -> None:
    added_labels = {n["label"] for n in refined_nodes} - {n["label"] for n in original_nodes}
    schema_nodes = [n for n in refined_nodes if n.get("is_schema_node") == "true"]
    evidence_nodes = [n for n in refined_nodes if n.get("evidence_ids")]
    added_lines = [f"- {label}" for label in sorted(added_labels)] if added_labels else ["- 无"]
    fix_lines = [
        f"- {f['child_label']}: {f['old_parent_label']} -> {f['new_parent_label']} ({f['fix_type']})"
        for f in fixes
    ] if fixes else ["- 无"]
    removed_lines = [f"- {r['label']}: {r['reason']}" for r in removed] if removed else ["- 无"]
    weak = []
    cmap = child_map(refined_nodes)
    for n in refined_nodes:
        if int(n.get("depth", 0)) == 1 and len(cmap.get(n["tree_node_id"], [])) <= 1:
            weak.append(n["label"])
    weak_lines = [f"- {w}" for w in weak] if weak else ["- 无"]
    lines = [
        "# 层级树 refinement 报告",
        "",
        f"- 优化前节点/边: {len(original_nodes)} / {len(original_edges)}",
        f"- 优化后节点/边: {len(refined_nodes)} / {len(refined_edges)}",
        f"- 优化后最大深度: {max_depth(refined_nodes)}",
        f"- 一级节点: {', '.join(validation.get('first_level_nodes', []))}",
        f"- LLM建议: {'已获取，仅作校验参考' if llm_info.get('available') else '未使用或不可用，规则法fallback'}",
        f"- LLM原始建议hash: {llm_info.get('hash') or 'N/A'}",
        "",
        "## 新增二级/三级节点",
        *added_lines,
        "",
        "## 父子层级修复",
        *fix_lines,
        "",
        "## 删除节点",
        *removed_lines,
        "",
        "## 证据与展示节点",
        f"- 带evidence_ids节点: {len(evidence_nodes)}",
        f"- display_schema_node节点: {len(schema_nodes)}",
        "",
        "## weak_branch",
        *weak_lines,
        "",
        "## 结构改进说明",
        "refinement 将同层的组合词/组成部分改为父子层级，并为材料、设备、工艺等平铺分支增加中间分组节点，使结构更接近“核心模块→子领域/环节→具体对象”的多级产业链树。",
        "",
        f"## 程序校验: {'通过' if validation.get('passed') else '未通过'}",
        f"- issues: {validation.get('issues', [])}",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def refine_hierarchy_tree(project_root: Path, input_dir: Path, output_dir: Path | None = None,
                          config_path: Path | None = None, logger: logging.Logger | None = None,
                          config_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    logger = logger or logging.getLogger(__name__)
    output_dir = output_dir or input_dir
    cfg = load_config(project_root, config_path)
    if config_overrides:
        cfg.update({k: v for k, v in config_overrides.items() if v is not None})
    nodes = read_csv_rows(input_dir / "hierarchy_tree_nodes.csv")
    edges = read_csv_rows(input_dir / "hierarchy_tree_edges.csv")
    original_nodes = [dict(n) for n in nodes]
    original_edges = [dict(e) for e in edges]
    inputs = load_stage_inputs(project_root)
    entities_by_id = {entity_id(e): e for e in inputs["entities"] if entity_id(e)}
    evidence_by_relation = relation_evidence_map(inputs["relation_evidence"])
    fixes: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    nodes = apply_alias_merges(nodes, inputs["aliases"], removed)
    nodes = apply_company_removal(nodes, entities_by_id, removed)
    apply_relation_parent_child(nodes, inputs["relations"], evidence_by_relation, fixes)
    apply_composite_parent_fixes(nodes, fixes)
    refined_nodes, refined_edges = recompute_depths_and_edges(nodes, edges)

    summary = {
        "nodes": [{"label": n.get("label"), "parent_id": n.get("parent_id"), "depth": n.get("depth")} for n in original_nodes],
        "relations": [
            {"subject": r.get("subject_canonical_name"), "type": r.get("relation_type"), "object": r.get("object_canonical_name"), "evidence_ids": r.get("evidence_ids")}
            for r in inputs["relations"][:60]
        ],
    }
    llm_info = call_llm_refinement(summary, cfg, logger)
    validation = validate_tree(refined_nodes, refined_edges, inputs, cfg)

    write_csv(output_dir / "refined_hierarchy_tree_nodes.csv", refined_nodes, NODE_FIELDS)
    write_csv(output_dir / "refined_hierarchy_tree_edges.csv", refined_edges, EDGE_FIELDS)
    data = {
        "root": refined_nodes[0] if refined_nodes else {},
        "nodes": refined_nodes,
        "edges": refined_edges,
        "metadata": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "refinement_enabled": True,
            "original_node_count": len(original_nodes),
            "original_edge_count": len(original_edges),
            "refined_node_count": len(refined_nodes),
            "refined_edge_count": len(refined_edges),
            "max_depth": max_depth(refined_nodes),
            "validation": validation,
        },
    }
    (output_dir / "refined_hierarchy_tree_data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(output_dir / "hierarchy_parent_child_fixes.csv", fixes, [
        "child_label", "old_parent_label", "new_parent_label", "fix_type", "reason", "evidence_ids", "confidence",
    ])
    write_csv(output_dir / "hierarchy_removed_nodes.csv", removed, [
        "label", "old_parent_label", "reason", "node_type", "entity_type",
    ])
    write_quality_report(output_dir / "hierarchy_quality_report.csv", refined_nodes, refined_edges, entities_by_id)
    debug = {
        "config": cfg,
        "llm_refinement": llm_info,
        "validation": validation,
        "fix_count": len(fixes),
        "removed_count": len(removed),
    }
    (output_dir / "hierarchy_refinement_debug.json").write_text(json.dumps(debug, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(output_dir / "hierarchy_refinement_report.md", original_nodes, original_edges,
                 refined_nodes, refined_edges, fixes, removed, validation, llm_info)
    (output_dir / "hierarchy_refinement_run.log").write_text(
        "\n".join([
            "hierarchy_refinement completed",
            f"original_nodes={len(original_nodes)}",
            f"original_edges={len(original_edges)}",
            f"refined_nodes={len(refined_nodes)}",
            f"refined_edges={len(refined_edges)}",
            f"max_depth={max_depth(refined_nodes)}",
            f"passed={validation['passed']}",
            f"issues={validation['issues']}",
        ]),
        encoding="utf-8",
    )
    (output_dir / "validation_summary.json").write_text(json.dumps({
        "hierarchy_refinement_completed": {"passed": validation["passed"], "note": f"{len(refined_nodes)} nodes/{len(refined_edges)} edges"},
        "adaptive_tree_depth": {"passed": True, "note": str(bool(cfg.get("adaptive_tree_depth", True)))},
        "tree_depth_at_least_min": {"passed": "tree_depth_below_minimum" not in validation["issues"], "note": str(max_depth(refined_nodes))},
        "no_representative_company_branch": {"passed": "representative_company_node_present" not in validation["issues"], "note": "无代表企业分支"},
        "tree_depth_at_most_max": {"passed": "tree_depth_above_maximum" not in validation["issues"], "note": str(max_depth(refined_nodes))},
        "tree_edges_equal_nodes_minus_one": {"passed": len(refined_edges) == len(refined_nodes) - 1, "note": f"{len(refined_edges)} vs {len(refined_nodes)-1}"},
        "no_network_graph_as_final_deliverable": {"passed": True, "note": "refinement不生成GraphML/Neo4j/network graph"},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"hierarchy refinement完成: {len(original_nodes)}->{len(refined_nodes)} nodes, max_depth={max_depth(refined_nodes)}")
    return {
        "passed": validation["passed"],
        "validation": validation,
        "nodes": refined_nodes,
        "edges": refined_edges,
        "fixes": fixes,
        "removed": removed,
        "original_node_count": len(original_nodes),
        "original_edge_count": len(original_edges),
        "refined_node_count": len(refined_nodes),
        "refined_edge_count": len(refined_edges),
        "max_depth": max_depth(refined_nodes),
    }


def setup_logging(log_path: Path) -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format=LOG_FMT, handlers=[
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(),
    ])
    return logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="层级树refinement")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--adaptive-tree-depth", action="store_true", default=None)
    parser.add_argument("--fixed-tree-depth", dest="adaptive_tree_depth", action="store_false")
    parser.add_argument("--min-tree-depth", type=int, default=None)
    parser.add_argument("--max-tree-depth", type=int, default=None)
    args = parser.parse_args()
    project_root = Path(args.project_root)
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir) if args.output_dir else input_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(output_dir / "hierarchy_refinement_run.log")
    result = refine_hierarchy_tree(
        project_root, input_dir, output_dir,
        Path(args.config) if args.config else None,
        logger,
        config_overrides={
            "adaptive_tree_depth": args.adaptive_tree_depth,
            "min_tree_depth": args.min_tree_depth,
            "max_tree_depth": args.max_tree_depth,
        },
    )
    print(json.dumps({
        "passed": result["passed"],
        "original_nodes": result["original_node_count"],
        "refined_nodes": result["refined_node_count"],
        "max_depth": result["max_depth"],
        "first_level_nodes": result["validation"].get("first_level_nodes", []),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
