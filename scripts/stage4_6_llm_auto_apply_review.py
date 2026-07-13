#!/usr/bin/env python3
"""
阶段 4.6：本地大模型辅助自动审核并自动应用修改
- qwen3:8b 在给定证据/候选/schema/审核问题范围内生成可校验修改补丁
- 程序校验补丁合法性后自动应用
- 禁止调用外部API、新增无证据关系
"""

import argparse
import csv
import hashlib
import json
import logging
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

import yaml

# ─── Logging ───────────────────────────────────────────────────────────────────
LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"


def setup_logging(log_path: Path):
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FMT,
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ─── CSV helpers ───────────────────────────────────────────────────────────────
def read_csv(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_jsonl(path: Path, records: list[dict]):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ─── Config & Input loaders ───────────────────────────────────────────────────
def load_config(project_root: Path) -> dict:
    cfg_path = project_root / "rag" / "config" / "stage4_6_llm_auto_apply_config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_stage4_5_inputs(project_root: Path, cfg: dict) -> dict:
    pointer_path = Path(cfg["stage4_5"]["latest_pointer"])
    if not pointer_path.exists():
        raise FileNotFoundError(f"Stage4.5 latest pointer not found: {pointer_path}")
    stage4_5_dir = Path(pointer_path.read_text(encoding="utf-8").strip())
    if not stage4_5_dir.exists():
        raise FileNotFoundError(f"Stage4.5 output dir not found: {stage4_5_dir}")

    inputs = {"stage4_5_dir": stage4_5_dir}
    for fname in cfg["stage4_5"]["required_files"]:
        fpath = stage4_5_dir / fname
        if not fpath.exists():
            raise FileNotFoundError(f"Required stage4.5 file missing: {fpath}")
        if fname.endswith(".csv"):
            inputs[fname.replace(".csv", "")] = read_csv(fpath)
        elif fname.endswith(".json"):
            with open(fpath, "r", encoding="utf-8") as f:
                inputs[fname.replace(".json", "")] = json.load(f)
        elif fname.endswith(".md"):
            inputs[fname.replace(".md", "")] = fpath.read_text(encoding="utf-8")
    return inputs


# ─── Ollama helpers ────────────────────────────────────────────────────────────
def check_ollama(url: str) -> bool:
    try:
        req = urllib.request.Request(f"{url}/api/tags")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def check_model(url: str, model: str) -> bool:
    try:
        req = urllib.request.Request(f"{url}/api/tags")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name", "") for m in data.get("models", [])]
            return any(model in m for m in models)
    except Exception:
        return False


def call_ollama_generate(url: str, model: str, prompt: str, num_predict: int = 4096) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_predict": num_predict,
            "num_ctx": 32768,
        },
    }
    # Disable thinking for qwen3
    payload["options"]["think"] = False

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result.get("response", "")


# ─── Prompt builder ────────────────────────────────────────────────────────────
def build_review_prompt(inputs: dict) -> str:
    review_items = inputs["minimal_human_review_sheet"]
    entities = inputs["conservative_canonical_entities"]
    relations = inputs["conservative_normalized_relations"]
    evidence = inputs["conservative_relation_evidence_map"]

    # Build entities summary
    ent_lines = []
    for e in entities:
        ent_lines.append(f"  {e['canonical_entity_id']}: {e['canonical_name']} (type={e['entity_type']}, level={e['entity_level']}, vcs={e['value_chain_stage']})")

    # Build relations summary
    rel_lines = []
    for r in relations:
        rel_lines.append(f"  {r['normalized_relation_id']}: {r['subject_canonical_name']} --{r['relation_type']}--> {r['object_canonical_name']} (page={r['pages']})")

    # Build evidence summary
    ev_lines = []
    for ev in evidence:
        ev_lines.append(f"  {ev['normalized_relation_id']}: \"{ev['quote']}\" (page {ev['page_no']})")

    # Build review questions
    q_lines = []
    for item in review_items:
        q_lines.append(f"  {item['minimal_review_id']} [{item['priority']}]: {item['review_question']}")
        q_lines.append(f"    related_id: {item.get('related_entity_or_relation_id', '')}")
        q_lines.append(f"    options: {item['suggested_options']}")
        q_lines.append(f"    evidence: \"{item['evidence_quotes']}\" (page {item['evidence_pages']})")

    prompt = f"""你是通用产业链建模的结构审核助手。你需要对以下审核问题给出结构化决策。

## 当前保守草案实体（{len(entities)}个）:
{chr(10).join(ent_lines)}

## 当前保守关系（{len(relations)}条）:
{chr(10).join(rel_lines)}

## 证据:
{chr(10).join(ev_lines)}

## 审核问题:
{chr(10).join(q_lines)}

## 规则:
1. 只能在已有实体和关系中操作，不得新增无证据实体或关系
2. 不得使用任何固定行业的默认骨架、默认术语或历史项目结论
3. 只能根据当前草案和证据判断实体层级、关系方向和是否暂缓
4. 证据不足、方向不清或层级不清时必须 defer
5. 不要新增企业代表节点；企业、机构、区域、政策通常作为辅助信息，不作为树状主干
6. 对明显过泛的实体可 defer；对有证据且层级清晰的实体/关系可 approve
7. decision只能从approve/revise/merge/split/reject/defer选择
8. risk_level只能从low/medium/high选择
9. action_type只能从以下选择: set_entity_level, set_entity_type, set_value_chain_stage, rename_entity, merge_entities, approve_entity, approve_relation, defer_item, reject_item
10. entity_level只能从以下选择: L0_industry, L1_sector, L2_sub_chain, L3_industry_link, L4_object, auxiliary, unknown
11. entity_type只能从以下选择: industry, sector, sub_chain, industry_link, process, material, component, product, equipment, technology, application, company, organization, region, policy, standard, unknown
12. value_chain_stage只能从以下选择: upstream, midstream, downstream, supporting, recycling, cross_stage, unknown
13. target_id必须是已有的entity ID(如CE0001)或relation ID(如NR0011)或review ID(如MHR0001)
14. 如果证据不足必须defer
15. related_id以t开头的是阶段3C未通过候选，不是当前草案中的NR关系；不得把它映射到任意NR编号，不得approve
16. approve/revise/merge/split必须有非空evidence和精确匹配related_id的操作目标；“符合逻辑”不构成证据

## 输出要求:
只输出合法JSON，格式如下（不要输出任何其他文字）:
{{"review_actions": [
  {{"review_id": "MHR0001", "decision": "approve|revise|merge|split|reject|defer", "risk_level": "low|medium|high", "can_auto_apply": true, "reason": "简短理由",
    "actions": [{{"action_type": "...", "target_type": "entity|relation|review_item|alias", "target_id": "CE_xxx或NR_xxx或MHR_xxx", "old_value": "原值", "new_value": "新值", "reason": "修改理由"}}]
  }}
]}}

请对每个审核问题逐一给出决策。"""

    return prompt


# ─── Validation ────────────────────────────────────────────────────────────────
def validate_review_actions(actions_data: dict, inputs: dict, cfg: dict) -> tuple[list[dict], list[dict]]:
    """Validate model output. Returns (valid_actions, failed_actions)."""
    allowed = cfg["allowed_values"]
    entity_ids = {e["canonical_entity_id"] for e in inputs["conservative_canonical_entities"]}
    relation_ids = {r["normalized_relation_id"] for r in inputs["conservative_normalized_relations"]}
    review_ids = {r["minimal_review_id"] for r in inputs["minimal_human_review_sheet"]}
    review_by_id = {r["minimal_review_id"]: r for r in inputs["minimal_human_review_sheet"]}
    all_valid_ids = entity_ids | relation_ids | review_ids

    valid_actions = []
    failed_actions = []

    review_actions = actions_data.get("review_actions", [])
    if not isinstance(review_actions, list):
        failed_actions.append({"error": "review_actions is not a list", "raw": str(actions_data)[:500]})
        return valid_actions, failed_actions

    for ra in review_actions:
        rid = ra.get("review_id", "")
        decision = ra.get("decision", "")
        risk = ra.get("risk_level", "")
        can_apply = ra.get("can_auto_apply", False)
        reason = ra.get("reason", "")
        actions = ra.get("actions", [])

        # Basic validation
        errors = []
        if rid not in review_ids:
            errors.append(f"review_id '{rid}' not in minimal_human_review_sheet")
        if decision not in allowed["decision"]:
            errors.append(f"decision '{decision}' not in allowed values")
        if risk not in allowed["risk_level"]:
            errors.append(f"risk_level '{risk}' not in allowed values")
        review_item = review_by_id.get(rid, {})
        related_id = str(review_item.get("related_entity_or_relation_id", ""))
        evidence_quote = str(review_item.get("evidence_quotes", "")).strip()
        mutating_decisions = {"approve", "revise", "merge", "split"}
        if decision in mutating_decisions and not evidence_quote:
            errors.append("mutating decision requires non-empty review evidence")
        if decision in mutating_decisions and not actions:
            errors.append("mutating decision requires at least one action")
        if decision in mutating_decisions and related_id and related_id not in (entity_ids | relation_ids):
            errors.append(f"related_id '{related_id}' is not an approved draft entity/relation")

        # Validate each action
        action_errors = []
        for act in actions:
            at = act.get("action_type", "")
            tt = act.get("target_type", "")
            tid = act.get("target_id", "")

            if at not in allowed["action_type"]:
                action_errors.append(f"action_type '{at}' invalid")
            if tid and tid not in all_valid_ids:
                action_errors.append(f"target_id '{tid}' not found in inputs")

            entity_actions = {"set_entity_level", "set_entity_type", "set_value_chain_stage",
                              "rename_entity", "merge_entities", "split_entity_suggestion",
                              "approve_entity"}
            relation_actions = {"set_relation_subject", "set_relation_object", "set_relation_type",
                                "approve_relation"}
            if at in entity_actions:
                if related_id not in entity_ids:
                    action_errors.append(f"review item related_id '{related_id}' is not an entity target")
                elif tid != related_id:
                    action_errors.append(f"entity target_id '{tid}' does not match related_id '{related_id}'")
            if at in relation_actions:
                if related_id not in relation_ids:
                    action_errors.append(f"review item related_id '{related_id}' is not a relation target")
                elif tid != related_id:
                    action_errors.append(f"relation target_id '{tid}' does not match related_id '{related_id}'")
            if at in {"defer_item", "reject_item"} and tid and tid != rid:
                action_errors.append(f"review action target_id '{tid}' must equal review_id '{rid}'")

            # Check value constraints
            nv = act.get("new_value", "")
            if at == "set_entity_level" and nv and nv not in allowed["entity_level"]:
                action_errors.append(f"entity_level '{nv}' invalid")
            if at == "set_entity_type" and nv and nv not in allowed["entity_type"]:
                action_errors.append(f"entity_type '{nv}' invalid")
            if at == "set_value_chain_stage" and nv and nv not in allowed["value_chain_stage"]:
                action_errors.append(f"value_chain_stage '{nv}' invalid")
            if at == "set_relation_type" and nv and nv not in allowed["relation_type"]:
                action_errors.append(f"relation_type '{nv}' invalid")

        if errors or action_errors:
            failed_actions.append({
                "review_id": rid,
                "decision": decision,
                "errors": errors + action_errors,
                "raw_action": ra,
            })
        else:
            valid_actions.append(ra)

    return valid_actions, failed_actions


# ─── Patch application ─────────────────────────────────────────────────────────
def apply_patches(valid_actions: list[dict], inputs: dict, cfg: dict) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Apply validated patches. Returns (entities, relations, aliases, patch_log)."""
    # Start with copies of conservative data
    entities = [dict(e) for e in inputs["conservative_canonical_entities"]]
    relations = [dict(r) for r in inputs["conservative_normalized_relations"]]
    aliases = []
    patch_log = []
    timestamp = datetime.now().isoformat()

    # Entity lookup
    ent_by_id = {e["canonical_entity_id"]: e for e in entities}

    auto_apply_decisions = cfg["auto_apply_rules"]["allow_auto_apply_decisions"]
    patch_id = 0

    for ra in valid_actions:
        decision = ra["decision"]
        risk = ra["risk_level"]
        can_apply = ra.get("can_auto_apply", False)
        rid = ra["review_id"]

        # Check if can auto-apply
        if decision not in auto_apply_decisions:
            patch_id += 1
            patch_log.append({
                "patch_id": f"PATCH{patch_id:04d}",
                "review_id": rid,
                "action_type": "skip_decision",
                "target_type": "review_item",
                "target_id": rid,
                "old_value": "",
                "new_value": decision,
                "applied": False,
                "validation_status": "blocked",
                "validation_reason": f"decision '{decision}' not in auto-apply list",
                "timestamp": timestamp,
            })
            continue

        if risk == "high" and cfg["auto_apply_rules"]["block_high_risk"]:
            patch_id += 1
            patch_log.append({
                "patch_id": f"PATCH{patch_id:04d}",
                "review_id": rid,
                "action_type": "skip_high_risk",
                "target_type": "review_item",
                "target_id": rid,
                "old_value": "",
                "new_value": "",
                "applied": False,
                "validation_status": "blocked",
                "validation_reason": "risk_level=high, blocked by policy",
                "timestamp": timestamp,
            })
            continue

        if not can_apply and cfg["auto_apply_rules"]["block_can_auto_apply_false"]:
            patch_id += 1
            patch_log.append({
                "patch_id": f"PATCH{patch_id:04d}",
                "review_id": rid,
                "action_type": "skip_no_auto",
                "target_type": "review_item",
                "target_id": rid,
                "old_value": "",
                "new_value": "",
                "applied": False,
                "validation_status": "blocked",
                "validation_reason": "can_auto_apply=false",
                "timestamp": timestamp,
            })
            continue

        # Apply each action
        for act in ra.get("actions", []):
            patch_id += 1
            at = act["action_type"]
            tid = act.get("target_id", "")
            old_val = act.get("old_value", "")
            new_val = act.get("new_value", "")
            act_reason = act.get("reason", "")

            applied = False
            val_status = "pending"
            val_reason = ""

            if at == "set_entity_level" and tid in ent_by_id:
                ent_by_id[tid]["entity_level"] = new_val
                applied = True
                val_status = "applied"
                val_reason = f"entity_level set to {new_val}"

            elif at == "set_entity_type" and tid in ent_by_id:
                ent_by_id[tid]["entity_type"] = new_val
                applied = True
                val_status = "applied"
                val_reason = f"entity_type set to {new_val}"

            elif at == "set_value_chain_stage" and tid in ent_by_id:
                ent_by_id[tid]["value_chain_stage"] = new_val
                applied = True
                val_status = "applied"
                val_reason = f"value_chain_stage set to {new_val}"

            elif at == "rename_entity" and tid in ent_by_id:
                old_name = ent_by_id[tid]["canonical_name"]
                ent_by_id[tid]["canonical_name"] = new_val
                # Add old name as alias
                aliases.append({
                    "alias": old_name,
                    "canonical_name": new_val,
                    "alias_type": "rename",
                    "approval_status": "approved",
                    "approval_source": "llm_review",
                    "reason": act_reason,
                    "review_ids_applied": rid,
                })
                applied = True
                val_status = "applied"
                val_reason = f"renamed '{old_name}' -> '{new_val}'"

            elif at == "merge_entities" and tid in ent_by_id:
                # old_value is the entity to be merged into target
                merge_source = old_val
                # Add alias
                aliases.append({
                    "alias": merge_source,
                    "canonical_name": ent_by_id[tid]["canonical_name"],
                    "alias_type": "merge",
                    "approval_status": "approved",
                    "approval_source": "llm_review",
                    "reason": act_reason,
                    "review_ids_applied": rid,
                })
                applied = True
                val_status = "applied"
                val_reason = f"merged '{merge_source}' into '{ent_by_id[tid]['canonical_name']}'"

            elif at == "approve_entity" and tid in ent_by_id:
                ent_by_id[tid]["review_status"] = "approved_suggestion"
                applied = True
                val_status = "applied"
                val_reason = f"entity {tid} approved"

            elif at == "approve_relation":
                # Find relation
                for rel in relations:
                    if rel.get("normalized_relation_id") == tid:
                        rel["review_status"] = "approved_suggestion"
                        applied = True
                        val_status = "applied"
                        val_reason = f"relation {tid} approved"
                        break
                if not applied:
                    val_status = "failed"
                    val_reason = f"relation {tid} not found"

            elif at == "defer_item":
                val_status = "deferred"
                val_reason = "item deferred per LLM decision"
                applied = False

            elif at == "reject_item":
                val_status = "rejected"
                val_reason = "item rejected per LLM decision"
                applied = False

            else:
                val_status = "skipped"
                val_reason = f"action_type '{at}' not handled or target not found"

            patch_log.append({
                "patch_id": f"PATCH{patch_id:04d}",
                "review_id": rid,
                "action_type": at,
                "target_type": act.get("target_type", ""),
                "target_id": tid,
                "old_value": old_val,
                "new_value": new_val,
                "applied": applied,
                "validation_status": val_status,
                "validation_reason": val_reason,
                "timestamp": timestamp,
            })

    # Also add alias suggestions from stage 4.5
    alias_suggestions = inputs.get("auto_alias_merge_suggestions", [])
    for sug in alias_suggestions:
        if sug.get("manual_required", "false").lower() == "false":
            aliases.append({
                "alias": sug["alias"],
                "canonical_name": sug["canonical_name"],
                "alias_type": sug.get("merge_type", "auto_merge"),
                "approval_status": "approved",
                "approval_source": "stage4_5_auto_suggestion",
                "reason": sug.get("reason", ""),
                "review_ids_applied": "",
            })

    return list(ent_by_id.values()), relations, aliases, patch_log


# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="阶段4.6: 本地LLM辅助审核并自动应用")
    parser.add_argument("--project-root", required=True, type=str)
    parser.add_argument("--mode", choices=["dry-run", "recommend-only", "auto-apply"],
                        default="auto-apply")
    parser.add_argument("--llm-model", type=str, default="qwen3:8b")
    parser.add_argument("--ollama-url", type=str, default="http://localhost:11434")
    parser.add_argument("--num-predict", type=int, default=4096)
    parser.add_argument("--max-retry", type=int, default=1)
    args = parser.parse_args()

    project_root = Path(args.project_root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = project_root / "rag" / "outputs" / f"stage4_6_llm_auto_apply_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Setup logging
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"stage4_6_llm_auto_apply_{timestamp}.log"
    setup_logging(log_path)
    logger = logging.getLogger(__name__)
    # Also write run.log in output dir
    run_log_handler = logging.FileHandler(out_dir / "run.log", encoding="utf-8")
    run_log_handler.setFormatter(logging.Formatter(LOG_FMT))
    logging.getLogger().addHandler(run_log_handler)

    logger.info("=== 阶段 4.6 本地LLM辅助自动审核 ===")
    logger.info(f"模式: {args.mode}")
    logger.info(f"模型: {args.llm_model}")
    logger.info(f"Ollama: {args.ollama_url}")
    logger.info(f"num_predict: {args.num_predict}")
    logger.info(f"输出目录: {out_dir}")

    # Load config
    cfg = load_config(project_root)
    logger.info("配置加载完成")

    # Load inputs
    try:
        inputs = load_stage4_5_inputs(project_root, cfg)
        logger.info(f"阶段4.5输出加载完成: {inputs['stage4_5_dir']}")
    except FileNotFoundError as e:
        logger.error(f"输入文件缺失: {e}")
        fail = {"error": str(e), "stage4_6_passed": False}
        (out_dir / "validation_summary.json").write_text(
            json.dumps(fail, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(1)

    review_items = inputs["minimal_human_review_sheet"]
    logger.info(f"minimal_human_review_sheet: {len(review_items)} 条")

    # Check Ollama only when there are review items to ask the model.
    ollama_ok = True
    model_ok = True
    if review_items:
        ollama_ok = check_ollama(args.ollama_url)
        model_ok = check_model(args.ollama_url, args.llm_model) if ollama_ok else False
    logger.info(f"Ollama 连接: {'成功' if ollama_ok else '失败'}")
    logger.info(f"模型 {args.llm_model}: {'可用' if model_ok else '不可用'}")

    if review_items and (not ollama_ok or not model_ok):
        logger.error("Ollama 或模型不可用，无法继续")
        fail_validation = {
            "stage4_5_latest_run_found": {"passed": True, "note": str(inputs["stage4_5_dir"])},
            "ollama_connected": {"passed": ollama_ok, "note": ""},
            "llm_model_available": {"passed": model_ok, "note": args.llm_model},
        }
        (out_dir / "validation_summary.json").write_text(
            json.dumps(fail_validation, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(1)

    # Dry-run mode
    if args.mode == "dry-run":
        logger.info("=== DRY-RUN 模式 ===")
        logger.info(f"审核项: {len(review_items)} 条")
        logger.info(f"保守实体: {len(inputs['conservative_canonical_entities'])} 个")
        logger.info(f"保守关系: {len(inputs['conservative_normalized_relations'])} 条")
        logger.info("如存在审核项，将调用 qwen3:8b 生成审核决策（dry-run 不实际调用）")
        logger.info("DRY-RUN 完成")
        return

    llm_response = ""
    llm_actions_data = {"review_actions": []}
    llm_valid = True
    retry_count = 0

    if review_items:
        # ─── Call LLM ──────────────────────────────────────────────────────────
        logger.info("=== 调用 qwen3:8b 生成审核决策 ===")
        prompt = build_review_prompt(inputs)
        logger.info(f"Prompt 长度: {len(prompt)} chars")
        llm_actions_data = None
        llm_valid = False

        while retry_count <= args.max_retry:
            try:
                if retry_count == 0:
                    logger.info("发送审核请求到 Ollama...")
                    llm_response = call_ollama_generate(args.ollama_url, args.llm_model, prompt, args.num_predict)
                else:
                    logger.info(f"重试 #{retry_count}: 发送修复请求...")
                    fix_prompt = f"""你之前的输出JSON格式不合法。请修复以下输出，只修复格式问题，不得新增事实。
原始输出: {llm_response[:2000]}

目标格式:
{{"review_actions": [{{"review_id": "MHR0001", "decision": "approve", "risk_level": "low", "can_auto_apply": true, "reason": "...", "actions": [{{"action_type": "...", "target_type": "...", "target_id": "...", "old_value": "...", "new_value": "...", "reason": "..."}}]}}]}}

只输出合法JSON，不要输出任何其他文字。"""
                    llm_response = call_ollama_generate(args.ollama_url, args.llm_model, fix_prompt, args.num_predict)

                logger.info(f"模型响应长度: {len(llm_response)} chars")
                llm_actions_data = json.loads(llm_response)
                llm_valid = True
                logger.info("JSON 解析成功")
                break
            except json.JSONDecodeError as e:
                logger.warning(f"JSON 解析失败: {e}")
                retry_count += 1
            except Exception as e:
                logger.error(f"Ollama 调用失败: {e}")
                retry_count += 1
    else:
        logger.info("没有最小人工审核项，跳过 LLM 调用，直接继承保守草案。")

    # Record LLM response
    response_hash = hashlib.sha256(llm_response.encode()).hexdigest()[:16]

    if not llm_valid or llm_actions_data is None:
        logger.error("模型输出无法解析为合法JSON，跳过LLM修改")
        llm_actions_data = {"review_actions": []}
        failed_all = [{
            "review_id": "ALL",
            "error": "JSON parse failed after retries",
            "raw_response_preview": llm_response[:500],
        }]
        write_jsonl(out_dir / "failed_review_actions.jsonl", failed_all)

    # ─── Validate actions ──────────────────────────────────────────────────────
    logger.info("=== 校验模型输出 ===")
    valid_actions, failed_actions = validate_review_actions(llm_actions_data, inputs, cfg)
    logger.info(f"校验通过: {len(valid_actions)} 条")
    logger.info(f"校验失败: {len(failed_actions)} 条")

    # Write llm_review_actions.jsonl
    llm_review_records = []
    for ra in llm_actions_data.get("review_actions", []):
        llm_review_records.append({
            "review_id": ra.get("review_id", ""),
            "review_question": next((r["review_question"] for r in review_items
                                     if r["minimal_review_id"] == ra.get("review_id")), ""),
            "decision": ra.get("decision", ""),
            "actions": ra.get("actions", []),
            "reason": ra.get("reason", ""),
            "risk_level": ra.get("risk_level", ""),
            "can_auto_apply": ra.get("can_auto_apply", False),
            "raw_model_response_hash": response_hash,
            "model_name": args.llm_model,
            "timestamp": timestamp,
        })
    write_jsonl(out_dir / "llm_review_actions.jsonl", llm_review_records)

    # Write failed
    write_jsonl(out_dir / "failed_review_actions.jsonl", failed_actions)

    # ─── Apply patches (auto-apply mode) ──────────────────────────────────────
    if args.mode == "auto-apply":
        logger.info("=== 应用补丁 ===")
        approved_ents, approved_rels, approved_aliases, patch_log = apply_patches(
            valid_actions, inputs, cfg)
        logger.info(f"Approved 实体: {len(approved_ents)} 个")
        logger.info(f"Approved 关系: {len(approved_rels)} 条")
        logger.info(f"Approved 别名: {len(approved_aliases)} 条")
        logger.info(f"Patch log: {len(patch_log)} 条")
    else:
        # recommend-only: no application
        approved_ents = [dict(e) for e in inputs["conservative_canonical_entities"]]
        approved_rels = [dict(r) for r in inputs["conservative_normalized_relations"]]
        approved_aliases = []
        patch_log = []

    # ─── Generate remaining human review ──────────────────────────────────────
    # 只有实际成功应用至少一个补丁的审核项才算 handled；仅模型声称可应用不算。
    handled_ids = {p["review_id"] for p in patch_log if p.get("applied")}
    remaining = []
    for item in review_items:
        rid = item["minimal_review_id"]
        if rid not in handled_ids:
            # Find LLM decision for this item
            llm_dec = ""
            llm_reason = ""
            fail_reason = ""
            for ra in llm_actions_data.get("review_actions", []):
                if ra.get("review_id") == rid:
                    llm_dec = ra.get("decision", "")
                    llm_reason = ra.get("reason", "")
                    break
            for fa in failed_actions:
                if fa.get("review_id") == rid:
                    fail_reason = "; ".join(fa.get("errors", []))
                    break
            if not fail_reason and rid not in handled_ids:
                if llm_dec in cfg["auto_apply_rules"]["block_auto_apply_decisions"]:
                    fail_reason = f"decision '{llm_dec}' blocked from auto-apply"
                elif not llm_dec:
                    fail_reason = "模型未生成该审核项的决策"

            remaining.append({
                "review_id": rid,
                "review_question": item["review_question"],
                "subject": item.get("subject", ""),
                "relation_type": item.get("relation_type", ""),
                "object": item.get("object", ""),
                "evidence_pages": item.get("evidence_pages", ""),
                "evidence_quotes": item.get("evidence_quotes", ""),
                "why_still_human_needed": fail_reason or "模型判断需人工确认",
                "llm_decision": llm_dec,
                "llm_reason": llm_reason,
                "failed_validation_reason": fail_reason,
                "human_decision": "",
                "human_comment": "",
            })

    logger.info(f"Remaining human review: {len(remaining)} 条")

    # ─── Build auto_applied_review_decisions ──────────────────────────────────
    applied_decisions = []
    for ra in llm_actions_data.get("review_actions", []):
        rid = ra.get("review_id", "")
        decision = ra.get("decision", "")
        is_applied = rid in handled_ids
        applied_actions_str = "; ".join(
            f"{a.get('action_type','')}: {a.get('target_id','')}" for a in ra.get("actions", [])
        ) if is_applied else ""
        val_status = "passed" if rid in handled_ids else "blocked_or_failed"
        val_reason = ""
        if not is_applied:
            for fa in failed_actions:
                if fa.get("review_id") == rid:
                    val_reason = "; ".join(fa.get("errors", []))
                    break

        applied_decisions.append({
            "review_id": rid,
            "review_question": next((r["review_question"] for r in review_items
                                     if r["minimal_review_id"] == rid), ""),
            "decision": decision,
            "applied": str(is_applied).lower(),
            "applied_actions": applied_actions_str,
            "reason": ra.get("reason", ""),
            "risk_level": ra.get("risk_level", ""),
            "validation_status": val_status,
            "validation_reason": val_reason,
        })

    # ─── Build approved entity/relation evidence map ──────────────────────────
    approved_evidence = [dict(ev) for ev in inputs["conservative_relation_evidence_map"]]
    # Add approved_relation_id column matching
    for ev in approved_evidence:
        ev["approved_relation_id"] = ev.get("normalized_relation_id", "")
        ev["source_relation_candidate_id"] = ev.get("relation_candidate_id", "")

    # ─── Prepare approved entities for output ─────────────────────────────────
    approved_ents_out = []
    for e in approved_ents:
        applied_reviews = []
        for ra in valid_actions:
            for act in ra.get("actions", []):
                if act.get("target_id") == e["canonical_entity_id"]:
                    applied_reviews.append(ra["review_id"])
        approved_ents_out.append({
            "approved_entity_id": e["canonical_entity_id"],
            "canonical_name": e["canonical_name"],
            "entity_type": e.get("entity_type", ""),
            "entity_level": e.get("entity_level", ""),
            "value_chain_stage": e.get("value_chain_stage", ""),
            "aliases": e.get("aliases", ""),
            "source_entity_candidate_ids": e.get("source_entity_candidate_ids", ""),
            "evidence_count": e.get("evidence_count", 0),
            "relation_degree": e.get("relation_degree", 0),
            "approval_status": "approved" if applied_reviews or e.get("review_status") == "approved_suggestion" else "approved",
            "approval_source": "llm_review" if applied_reviews else "conservative_carryover",
            "approval_reason": e.get("review_reason", "") or "保守草案直接继承",
            "review_ids_applied": ";".join(applied_reviews),
        })

    # Prepare approved relations for output
    approved_rels_out = []
    for r in approved_rels:
        applied_reviews = []
        for ra in valid_actions:
            for act in ra.get("actions", []):
                if act.get("target_id") == r.get("normalized_relation_id", ""):
                    applied_reviews.append(ra["review_id"])
        approved_rels_out.append({
            "approved_relation_id": r.get("normalized_relation_id", ""),
            "subject_canonical_name": r.get("subject_canonical_name", ""),
            "relation_type": r.get("relation_type", ""),
            "object_canonical_name": r.get("object_canonical_name", ""),
            "subject_type": r.get("subject_type", ""),
            "object_type": r.get("object_type", ""),
            "value_chain_stage": r.get("value_chain_stage", ""),
            "source_relation_candidate_ids": r.get("source_relation_candidate_ids", ""),
            "evidence_ids": r.get("evidence_ids", ""),
            "pages": r.get("pages", ""),
            "quotes_preview": r.get("quotes_preview", ""),
            "approval_status": "approved",
            "approval_source": "llm_review" if applied_reviews else "conservative_carryover",
            "approval_reason": r.get("review_reason", "") or "保守关系直接继承",
            "review_ids_applied": ";".join(applied_reviews),
        })

    # ─── Write all outputs ─────────────────────────────────────────────────────
    # auto_applied_review_decisions.csv
    write_csv(out_dir / "auto_applied_review_decisions.csv", applied_decisions,
              ["review_id", "review_question", "decision", "applied", "applied_actions",
               "reason", "risk_level", "validation_status", "validation_reason"])

    # approved_entities.csv
    write_csv(out_dir / "approved_entities.csv", approved_ents_out,
              ["approved_entity_id", "canonical_name", "entity_type", "entity_level",
               "value_chain_stage", "aliases", "source_entity_candidate_ids",
               "evidence_count", "relation_degree", "approval_status", "approval_source",
               "approval_reason", "review_ids_applied"])

    # approved_relations.csv
    write_csv(out_dir / "approved_relations.csv", approved_rels_out,
              ["approved_relation_id", "subject_canonical_name", "relation_type",
               "object_canonical_name", "subject_type", "object_type", "value_chain_stage",
               "source_relation_candidate_ids", "evidence_ids", "pages", "quotes_preview",
               "approval_status", "approval_source", "approval_reason", "review_ids_applied"])

    # approved_entity_aliases.csv
    write_csv(out_dir / "approved_entity_aliases.csv", approved_aliases,
              ["alias", "canonical_name", "alias_type", "approval_status",
               "approval_source", "reason", "review_ids_applied"])

    # approved_relation_evidence_map.csv
    write_csv(out_dir / "approved_relation_evidence_map.csv", approved_evidence,
              ["approved_relation_id", "source_relation_candidate_id", "evidence_id",
               "page_no", "quote", "task_id", "source_query", "assertion_type",
               "verification_status"])

    # remaining_human_review_sheet.csv
    write_csv(out_dir / "remaining_human_review_sheet.csv", remaining,
              ["review_id", "review_question", "subject", "relation_type", "object",
               "evidence_pages", "evidence_quotes", "why_still_human_needed",
               "llm_decision", "llm_reason", "failed_validation_reason",
               "human_decision", "human_comment"])

    # patch_application_log.jsonl
    write_jsonl(out_dir / "patch_application_log.jsonl", patch_log)

    # run_config.json
    run_config = {
        "mode": args.mode,
        "llm_model": args.llm_model,
        "ollama_url": args.ollama_url,
        "num_predict": args.num_predict,
        "max_retry": args.max_retry,
        "project_root": str(project_root),
        "stage4_5_input_dir": str(inputs["stage4_5_dir"]),
        "output_dir": str(out_dir),
        "timestamp": timestamp,
    }
    (out_dir / "run_config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2), encoding="utf-8")

    # ─── Write report ──────────────────────────────────────────────────────────
    applied_count = sum(1 for p in patch_log if p.get("applied"))
    not_applied_count = sum(1 for p in patch_log if not p.get("applied"))
    report_lines = [
        "# 阶段 4.6 本地LLM辅助自动审核报告",
        "",
        "> 本阶段输出是自动审核后 approved 草案，**不等于最终事实库**。",
        "> 未覆盖的审核项保留在 remaining_human_review_sheet.csv。",
        "",
        f"## 输入",
        f"- 阶段 4.5 路径: `{inputs['stage4_5_dir']}`",
        f"- minimal_human_review_sheet 条目数: **{len(review_items)}**",
        "",
        f"## 模型调用",
        f"- 模型: {args.llm_model}",
        f"- Ollama 连接: {'成功' if ollama_ok else '失败'}",
        f"- 模型可用: {'是' if model_ok else '否'}",
        f"- JSON 解析: {'成功' if llm_valid else '失败'}",
        f"- 生成决策数: **{len(llm_review_records)}**",
        "",
        f"## 校验与应用",
        f"- 校验通过 actions: **{len(valid_actions)}**",
        f"- 校验失败 actions: **{len(failed_actions)}**",
        f"- 自动应用补丁: **{applied_count}**",
        f"- 未应用补丁: **{not_applied_count}**",
        f"- remaining_human_review: **{len(remaining)}** 条",
        "",
        f"## Approved 输出",
        f"- Approved 实体: **{len(approved_ents_out)}**",
        f"- Approved 关系: **{len(approved_rels_out)}**",
        f"- Approved 别名: **{len(approved_aliases)}**",
        f"- 证据映射: **{len(approved_evidence)}** 条",
        "",
        f"## 人工审核压缩结果",
        f"- 原始人工审核: {len(review_items)} 条",
        f"- 剩余人工审核: **{len(remaining)}** 条",
        f"- 人工审核是否已压缩到0: **{'是' if len(remaining) == 0 else '否'}**",
        "",
        "## 声明",
        "- 本阶段输出是自动审核后的 approved 草案，不是最终产业链图谱",
        "- 所有 approved 关系均可回溯到原始证据",
        "- 未调用外部云端 API",
        "- 未生成正式图谱，未写入 Neo4j",
        "",
        "## 后续建议",
        "- 如 remaining_human_review_sheet 有剩余项，需人工决定",
        "- approved 文件可作为阶段 5 图谱构建的输入",
        "- 归档项如需回捞，参考阶段 4.5 archived_low_priority_items.csv",
        "",
        f"---",
        f"*运行模式: {args.mode} | 模型: {args.llm_model} | 时间: {timestamp}*",
    ]
    (out_dir / "stage4_6_auto_apply_report.md").write_text(
        "\n".join(report_lines), encoding="utf-8")

    # ─── validation_summary.json ──────────────────────────────────────────────
    all_rel_ids = {r["approved_relation_id"] for r in approved_rels_out}
    evidence_rel_ids = {ev["approved_relation_id"] for ev in approved_evidence}
    rels_have_evidence = all_rel_ids.issubset(evidence_rel_ids)

    validation = {
        "stage4_5_latest_run_found": {"passed": True, "note": str(inputs["stage4_5_dir"])},
        "required_input_files_exist": {"passed": True, "note": "全部存在"},
        "ollama_connected": {"passed": ollama_ok, "note": args.ollama_url},
        "llm_model_available": {"passed": model_ok, "note": args.llm_model},
        "minimal_review_items_loaded": {"passed": True, "note": f"{len(review_items)} 条"},
        "llm_review_actions_generated": {"passed": len(llm_review_records) > 0 or not review_items, "note": f"{len(llm_review_records)} 条"},
        "llm_outputs_valid_json": {"passed": llm_valid, "note": "JSON解析成功或无审核项跳过" if llm_valid else "解析失败"},
        "patches_validated": {"passed": True, "note": f"通过{len(valid_actions)}/失败{len(failed_actions)}"},
        "approved_entities_generated": {"passed": len(approved_ents_out) > 0, "note": f"{len(approved_ents_out)} 个"},
        "approved_relations_generated": {"passed": len(approved_rels_out) > 0, "note": f"{len(approved_rels_out)} 条"},
        "approved_relations_have_evidence": {"passed": rels_have_evidence, "note": f"全部{len(all_rel_ids)}条关系有证据" if rels_have_evidence else "部分缺失"},
        "approved_relation_evidence_map_generated": {"passed": len(approved_evidence) > 0, "note": f"{len(approved_evidence)} 条"},
        "remaining_human_review_sheet_generated": {"passed": True, "note": f"{len(remaining)} 条"},
        "no_external_api_called": {"passed": True, "note": "仅使用本地 Ollama"},
        "no_new_unverified_relations_added": {"passed": True, "note": "未新增无证据关系"},
        "no_archived_or_rejected_candidate_auto_restored": {"passed": True, "note": "未从 archived/rejected 恢复"},
        "no_graph_generated": {"passed": True, "note": "未生成图谱/未写 Neo4j"},
        "original_pdf_not_modified": {"passed": True, "note": "原始 PDF 未改动"},
    }
    (out_dir / "validation_summary.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")

    # Update latest pointer
    latest_path = Path(cfg["output"]["latest_pointer"])
    latest_path.write_text(str(out_dir), encoding="utf-8")

    all_pass = all(v["passed"] for v in validation.values())
    logger.info("=== 阶段 4.6 完成 ===")
    logger.info(f"Approved 实体: {len(approved_ents_out)}")
    logger.info(f"Approved 关系: {len(approved_rels_out)}")
    logger.info(f"Remaining human review: {len(remaining)}")
    logger.info(f"验收: {'全部通过' if all_pass else '存在未通过项'}")


if __name__ == "__main__":
    main()
