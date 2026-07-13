#!/usr/bin/env python3
"""Regression tests for accuracy-oriented pipeline and hierarchy guardrails."""
import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def test_p1_concrete_item_reaches_review():
    from stage4_5_minimize_review import generate_auto_review_decisions

    inputs = {
        "manual_review_sheet": [{
            "review_id": "R1",
            "priority": "P1",
            "review_type": "relation",
            "subject": "显示材料",
            "relation_type": "INPUT_TO",
            "object": "面板制造",
            "entity_or_relation_id": "NR1",
            "issue_summary": "具体产业关系需要确认",
        }]
    }
    cfg = {"entity_auto_rules": {"defer_entities": ["上游", "中游", "下游"]}}
    decision = generate_auto_review_decisions(inputs, cfg)[0]
    assert_true(decision["auto_decision"] == "manual_required", "P1具体关系不应被静默暂缓")
    assert_true(decision["manual_required"] == "true", "P1具体关系应进入人工/本地LLM审核")


def test_forbidden_label_rejected_at_any_depth():
    from build_document_driven_template import validate_dynamic_tree

    nodes = [
        {"tree_node_id": "T1", "label": "显示产业链", "parent_id": "", "depth": 0,
         "category": "root", "node_type": "display_node", "source_entity_ids": "", "evidence_ids": ""},
        {"tree_node_id": "T2", "label": "OLED", "parent_id": "T1", "depth": 1,
         "category": "technology", "node_type": "approved_entity", "source_entity_ids": "E1", "evidence_ids": ""},
        {"tree_node_id": "T3", "label": "上下游", "parent_id": "T2", "depth": 2,
         "category": "group", "node_type": "display_node", "source_entity_ids": "", "evidence_ids": ""},
    ]
    edges = [
        {"tree_edge_id": "TE1", "parent_tree_node_id": "T1", "child_tree_node_id": "T2", "is_evidence_fact": "false", "evidence_ids": ""},
        {"tree_edge_id": "TE2", "parent_tree_node_id": "T2", "child_tree_node_id": "T3", "is_evidence_fact": "false", "evidence_ids": ""},
    ]
    context = {
        "maps": {"by_id": {"E1": {}}},
        "evidence_universe": set(),
        "validation_rules": {"preferred_depth_min": 2},
        "max_depth": 4,
    }
    result = validate_dynamic_tree(nodes, edges, context)
    assert_true(not result["passed"], "上游/中游/下游/上下游出现在深层节点时也必须失败")
    assert_true(any("forbidden_tree_nodes" in issue for issue in result["issues"]), "应报告禁用节点")


def test_dynamic_depth_is_two_to_four():
    from build_document_driven_template import validate_dynamic_tree

    context = {
        "maps": {"by_id": {"E1": {}}},
        "evidence_universe": set(),
        "validation_rules": {"preferred_depth_min": 2},
        "max_depth": 4,
    }
    nodes = [
        {"tree_node_id": "T1", "label": "显示产业链", "parent_id": "", "depth": 0,
         "category": "root", "node_type": "display_node", "source_entity_ids": "", "evidence_ids": ""},
        {"tree_node_id": "T2", "label": "OLED", "parent_id": "T1", "depth": 1,
         "category": "technology", "node_type": "approved_entity", "source_entity_ids": "E1", "evidence_ids": ""},
    ]
    edges = [{"tree_edge_id": "TE1", "parent_tree_node_id": "T1", "child_tree_node_id": "T2",
              "is_evidence_fact": "false", "evidence_ids": ""}]
    result = validate_dynamic_tree(nodes, edges, context)
    assert_true("tree_depth_below_minimum" in result["issues"], "内容深度不足2级时必须报告")


def test_missing_validation_is_not_success():
    from run_industry_chain_one_click import failed_validation_keys

    summary = {"a": {"passed": True}, "b": {"passed": False}}
    assert_true(failed_validation_keys(summary, ["a", "b", "c"]) == ["b", "c"],
                "缺失或失败的门禁项都不能视为成功")


def test_relation_cooccurrence_is_not_verified_as_supply():
    from stage3c_rag_extract_candidates import validate_relation

    quote = "支持企业在短板材料、核心技术等方面的研发和产业化"
    ctx = {"EV1": {
        "evidence_id": "EV1", "page_start": 1, "text": quote,
        "content_type": "paragraph", "review_required": False,
    }}
    relation = {
        "subject": "短板材料", "subject_type": "material",
        "relation_type": "SUPPLIES_TO",
        "object": "核心技术", "object_type": "technology",
        "evidence_id": "EV1", "page_no": 1, "quote": quote,
        "assertion_type": "explicit_fact", "review_required": False,
    }
    status, reason, _ = validate_relation(relation, ctx)
    assert_true(status == "review" and reason == "relation_cue_missing",
                "并列共现不能直接验证为供应关系")

    input_quote = "面板企业对材料和工艺的理解能力"
    input_ctx = {"EV_INPUT": {
        "evidence_id": "EV_INPUT", "page_start": 2, "text": input_quote,
        "content_type": "paragraph", "review_required": False,
    }}
    status, reason, _ = validate_relation({
        "subject": "材料", "subject_type": "material", "relation_type": "INPUT_TO",
        "object": "工艺", "object_type": "process", "evidence_id": "EV_INPUT",
        "page_no": 2, "quote": input_quote, "assertion_type": "explicit_fact",
        "review_required": False,
    }, input_ctx)
    assert_true(status == "review" and reason == "relation_cue_missing",
                "材料与工艺共同出现不能仅凭实体名词验证为输入关系")


def test_relation_endpoints_must_both_be_in_quote():
    from stage3c_rag_extract_candidates import validate_relation

    evidence = "显示面板企业重视基础研究。材料研发依赖国家科技项目支持。"
    ctx = {"CROSS_SENTENCE": {"text": evidence, "page_start": 1,
                               "content_type": "paragraph", "review_required": False}}
    status, reason, _ = validate_relation({
        "subject": "材料", "subject_type": "material", "relation_type": "INPUT_TO",
        "object": "显示面板企业", "object_type": "company", "evidence_id": "CROSS_SENTENCE",
        "page_no": 1, "quote": "材料研发依赖国家科技项目支持。",
        "assertion_type": "explicit_fact", "review_required": False,
    }, ctx)
    assert_true(status == "rejected" and reason == "subject_or_object_not_found",
                "关系端点不得从证据块的不同句子拼接")


def test_part_of_direction_follows_child_to_parent():
    from stage3c_rag_extract_candidates import validate_relation

    quote = "新型显示技术主要包括薄膜晶体管液晶显示"
    ctx = {"EV2": {
        "evidence_id": "EV2", "page_start": 2, "text": quote,
        "content_type": "paragraph", "review_required": False,
    }}
    reversed_relation = {
        "subject": "新型显示技术", "subject_type": "industry",
        "relation_type": "PART_OF",
        "object": "薄膜晶体管液晶显示", "object_type": "sector",
        "evidence_id": "EV2", "page_no": 2, "quote": quote,
        "assertion_type": "explicit_fact", "review_required": False,
    }
    status, reason, _ = validate_relation(reversed_relation, ctx)
    assert_true(status == "review" and reason == "part_of_direction_or_scope_mismatch",
                "父包括子不能被写成父PART_OF子")

    correct_relation = dict(reversed_relation)
    correct_relation.update({
        "subject": "薄膜晶体管液晶显示", "subject_type": "sector",
        "object": "新型显示技术", "object_type": "industry",
    })
    status, reason, _ = validate_relation(correct_relation, ctx)
    assert_true(status == "verified" and reason == "ok", "子PART_OF父应通过方向校验")


def test_allowed_doc_ids_are_effective_filters():
    from stage3c_rag_extract_candidates import effective_doc_ids, build_doc_filter

    allowed = {"doc_a", "doc_b"}
    assert_true(effective_doc_ids("", allowed) == allowed, "allowed-doc-ids必须参与检索过滤")
    assert_true(effective_doc_ids("doc_single", allowed) == {"doc_single"}, "显式doc-id应具有最高优先级")
    qfilter = build_doc_filter(allowed)
    match_values = set(qfilter.must[0].match.any)
    assert_true(match_values == allowed, "Qdrant过滤器必须包含全部允许文档")


def test_visual_review_notes_are_document_scoped():
    from stage1_document_audit import load_visual_notes

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "notes.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["doc_id", "page_no", "page_type_override"])
            writer.writeheader()
            writer.writerow({"doc_id": "doc_a", "page_no": "4", "page_type_override": "toc"})
            writer.writerow({"doc_id": "doc_b", "page_no": "4", "page_type_override": "paragraph"})
        notes = load_visual_notes(path, "doc_b")
        assert_true(notes[4]["page_type_override"] == "paragraph", "视觉页码标注不得跨文档套用")


def test_adjacent_pdf_lines_form_one_evidence_chunk():
    from stage2_build_evidence import build_evidence_chunks

    def block(idx, text):
        return {
            "block_id": f"B{idx}", "block_role": "paragraph",
            "text_normalized": text, "text_raw": text,
            "section_path": ["前言"], "bbox": [0, idx * 10, 100, idx * 10 + 8],
            "text_quality": "normal", "review_required": False,
        }
    pages = {4: {
        "page_type": "paragraph",
        "blocks": [block(1, "新型显示技术主要包括薄膜晶体管液晶显示、"),
                   block(2, "主动矩阵有机发光二极管显示。")],
    }}
    chunks = build_evidence_chunks("doc", pages, {})
    assert_true(len(chunks) == 1, "同一段落的PDF视觉行应合并为一个证据块")
    assert_true(chunks[0]["source_block_ids"] == ["B1", "B2"], "合并证据必须保留全部源块")


def test_pdf_text_and_schema_alias_normalization():
    from stage3c_rag_extract_candidates import _text_has, validate_entity, is_atomic_entity_name

    assert_true(_text_has("TFT-LCD（面板）", "TFT LCD 面板"), "全半角、连接符和空白差异应可匹配")
    ctx = {"EV": {"text": "国家公共创新平台", "page_start": 1,
                   "content_type": "paragraph", "review_required": False}}
    status, _, normalized = validate_entity({
        "surface_form": "国家公共创新平台", "entity_type": "platform",
        "evidence_id": "EV", "page_no": 1, "quote": "国家公共创新平台",
    }, ctx)
    assert_true(status == "verified" and normalized["entity_type"] == "technology",
                "平台类型应规范到受控technology")
    assert_true(not is_atomic_entity_name("TFT-LCD、AMOLED、Micro-LED"), "列表不能作为单一关系端点")


def test_explicit_input_predicates_and_company_endpoint():
    from stage3c_rag_extract_candidates import validate_relation

    cases = [
        ("每块液晶面板需要两块玻璃基板", "玻璃基板", "material", "液晶面板", "product"),
        ("面板制造企业积极采用就近配套材料", "配套材料", "material", "面板制造企业", "company"),
    ]
    for index, (quote, subject, subject_type, obj, object_type) in enumerate(cases, 1):
        eid = f"INPUT{index}"
        ctx = {eid: {"text": quote, "page_start": index,
                     "content_type": "paragraph", "review_required": False}}
        status, reason, _ = validate_relation({
            "subject": subject, "subject_type": subject_type,
            "relation_type": "INPUT_TO", "object": obj, "object_type": object_type,
            "evidence_id": eid, "page_no": index, "quote": quote,
            "assertion_type": "explicit_fact", "review_required": False,
        }, ctx)
        assert_true(status == "verified" and reason == "ok", f"明确输入关系应通过：{quote}")


def test_forecast_filter_is_scoped_to_relation_quote():
    from stage3c_rag_extract_candidates import validate_relation

    evidence_text = ("每块液晶面板需要两块玻璃基板。"
                     "预计未来几年这种供需差距还将继续保持。")
    ctx = {"FORECAST_SCOPE": {"text": evidence_text, "page_start": 29,
                               "content_type": "paragraph", "review_required": False}}
    base = {
        "subject": "玻璃基板", "subject_type": "material", "relation_type": "INPUT_TO",
        "object": "液晶面板", "object_type": "product", "evidence_id": "FORECAST_SCOPE",
        "page_no": 29, "quote": "每块液晶面板需要两块玻璃基板",
        "assertion_type": "explicit_fact", "review_required": False,
    }
    status, reason, _ = validate_relation(base, ctx)
    assert_true(status == "verified" and reason == "ok", "同段后续预测不得污染前一句现实事实")

    policy_text = "鼓励面板制造企业积极采用就近配套材料。"
    policy_ctx = {"POLICY": {"text": policy_text, "page_start": 16,
                              "content_type": "paragraph", "review_required": False}}
    policy = dict(base, subject="配套材料", object="面板制造企业", object_type="company",
                  evidence_id="POLICY", page_no=16, quote=policy_text)
    status, reason, _ = validate_relation(policy, policy_ctx)
    assert_true(status == "review" and reason == "forecast_keyword_in_quote:鼓励",
                "政策鼓励不得验证为已经发生的输入关系")


def test_explicit_part_of_pattern_fallback():
    from stage3c_rag_extract_candidates import extract_explicit_part_of_candidates, validate_relation

    text = ("前言新型显示技术主要包括薄膜晶体管液晶显示（TFT-LCD）、"
            "主动矩阵有机发光二极管显示（AMOLED）、激光显示等，其中前两者较成熟。")
    evidence = {"evidence_id": "STRUCT1", "page_start": 4, "text": text,
                "content_type": "paragraph", "review_required": False}
    entities, relations = extract_explicit_part_of_candidates([evidence])
    assert_true(len(relations) == 3, "显式包括列表应逐项生成三条父子候选")
    assert_true(all(r["object"] == "新型显示技术" for r in relations), "PART_OF对象必须是父类")
    assert_true(all(r["subject"] != "新型显示技术" for r in relations), "PART_OF方向必须为子到父")
    ctx = {"STRUCT1": evidence}
    assert_true(all(validate_relation(r, ctx)[0] == "verified" for r in relations),
                "确定性父子候选仍须逐条通过统一证据校验")
    assert_true(any(e["entity_type"] == "technology" for e in entities), "技术类父子项应保留具体类型")

    noisy = dict(evidence, evidence_id="STRUCT2", text="产品范围既包括显示材料也涉及建材、化工等传统材料。")
    _, noisy_relations = extract_explicit_part_of_candidates([noisy])
    assert_true(not noisy_relations, "“产品范围既包括”不能误作稳定分类层级")
    policy = dict(evidence, evidence_id="STRUCT3", text="首次以保险的形式对包括显示材料的首批次应用提出了支持。")
    _, policy_relations = extract_explicit_part_of_candidates([policy])
    assert_true(not policy_relations, "政策覆盖对象中的“包括”不能误作产业分类层级")


def test_explicit_unit_input_pattern_preserves_source_endpoints():
    from stage3c_rag_extract_candidates import extract_explicit_input_candidates, validate_relation

    text = "以玻璃基板为例，每块液晶面板需要两块玻璃基板，后续为统计说明。"
    evidence = {"evidence_id": "BOM1", "page_start": 29, "text": text,
                "content_type": "paragraph", "review_required": False}
    entities, relations = extract_explicit_input_candidates([evidence])
    assert_true(len(relations) == 1, "单位产品用量句应生成一条确定性输入候选")
    relation = relations[0]
    assert_true(relation["subject"] == "玻璃基板" and relation["object"] == "液晶面板",
                "确定性输入候选必须保留原文具体端点，不得改写为泛称")
    status, reason, _ = validate_relation(relation, {"BOM1": evidence})
    assert_true(status == "verified" and reason == "ok", "单位产品用量候选应通过统一校验")
    assert_true({e["surface_form"] for e in entities} == {"玻璃基板", "液晶面板"},
                "确定性输入实体必须来自原文")

    general = dict(evidence, evidence_id="BOM2", text="产业发展需要政策支持。")
    _, general_relations = extract_explicit_input_candidates([general])
    assert_true(not general_relations, "一般性“需要”句不得误作物料输入")


def test_same_name_entity_types_are_resolved_before_tree_building():
    from stage4_normalize_and_review import build_canonical_entities

    entities = [
        {"entity_candidate_id": "E1", "surface_form": "玻璃基板", "entity_type": "material", "evidence_id": "A"},
        {"entity_candidate_id": "E2", "surface_form": "玻璃基板", "entity_type": "product", "evidence_id": "B"},
        {"entity_candidate_id": "E3", "surface_form": "液晶材料", "entity_type": "material", "evidence_id": "C"},
        {"entity_candidate_id": "E4", "surface_form": "液晶材料", "entity_type": "product", "evidence_id": "D"},
    ]
    relations = [{
        "subject": "玻璃基板", "subject_type": "material",
        "object": "液晶面板", "object_type": "product",
    }]
    cfg = {"entity_level_rules": {}, "value_chain_stage_rules": {}}
    canonicals, _ = build_canonical_entities(entities, cfg, relations)
    assert_true(len(canonicals) == 2, "同名实体不得因类型分歧生成重复树节点")
    resolved = {c["canonical_name"]: c for c in canonicals}
    assert_true(resolved["玻璃基板"]["entity_type"] == "material", "关系端点和名称后缀应确认玻璃基板为材料")
    assert_true(resolved["液晶材料"]["entity_type"] == "material", "明确“材料”后缀应优先于模型误分类")
    assert_true(all(c["review_status"] == "auto_candidate" for c in canonicals),
                "规则已唯一解析的类型不应继续制造人工冲突")


def test_stage4_6_cannot_map_rejected_candidate_to_unrelated_relation():
    import yaml
    from stage4_6_llm_auto_apply_review import validate_review_actions

    cfg_path = Path(__file__).parents[1] / "rag" / "config" / "stage4_6_llm_auto_apply_config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    inputs = {
        "minimal_human_review_sheet": [{
            "minimal_review_id": "MHR1", "related_entity_or_relation_id": "t04_r02",
            "evidence_quotes": "", "evidence_pages": "",
        }],
        "conservative_canonical_entities": [],
        "conservative_normalized_relations": [{"normalized_relation_id": "NR0005"}],
    }
    unsafe = {"review_actions": [{
        "review_id": "MHR1", "decision": "approve", "risk_level": "low",
        "can_auto_apply": True, "reason": "符合逻辑",
        "actions": [{"action_type": "approve_relation", "target_type": "relation",
                     "target_id": "NR0005", "new_value": "材料->INPUT_TO->工艺"}],
    }]}
    valid, failed = validate_review_actions(unsafe, inputs, cfg)
    assert_true(not valid and failed, "未通过候选不得映射到任意现有NR关系并自动批准")
    errors = " ".join(failed[0]["errors"])
    assert_true("evidence" in errors and "related_id" in errors,
                "阻断原因必须同时指出缺少证据和目标ID不匹配")

    defer = {"review_actions": [{
        "review_id": "MHR1", "decision": "defer", "risk_level": "medium",
        "can_auto_apply": False, "reason": "证据不足", "actions": [],
    }]}
    valid, failed = validate_review_actions(defer, inputs, cfg)
    assert_true(len(valid) == 1 and not failed, "证据不足候选应允许安全暂缓")


def test_relation_first_tree_uses_only_evidence_connected_concrete_entities():
    from build_document_driven_template import (
        build_entity_maps, build_tree_from_approved_relations, validate_dynamic_tree,
    )

    inputs = {
        "entities": [
            {"approved_entity_id": "CE1", "canonical_name": "新型显示技术", "entity_type": "technology"},
            {"approved_entity_id": "CE2", "canonical_name": "AMOLED", "entity_type": "technology"},
            {"approved_entity_id": "CE3", "canonical_name": "AR/VR", "entity_type": "application"},
            {"approved_entity_id": "CE4", "canonical_name": "材料", "entity_type": "material"},
        ],
        "relations": [{
            "approved_relation_id": "NR1", "subject_canonical_name": "AMOLED",
            "subject_type": "technology", "relation_type": "PART_OF",
            "object_canonical_name": "新型显示技术", "object_type": "technology",
            "evidence_ids": "EV1",
        }],
        "relation_evidence": [{"approved_relation_id": "NR1", "evidence_id": "EV1"}],
    }
    context = {
        "maps": build_entity_maps(inputs), "evidence_universe": {"EV1"},
        "validation_rules": {"preferred_depth_min": 2}, "max_depth": 4,
    }
    nodes, edges, unclassified, _ = build_tree_from_approved_relations(
        inputs, context, "新型显示产业链",
        {"max_depth": 4, "tree_build_rules": {"parent_child_relation_types": ["PART_OF", "INPUT_TO"]}},
    )
    labels = {n["label"] for n in nodes}
    assert_true(labels == {"新型显示产业链", "新型显示技术", "AMOLED"},
                "最终树应只包含有关系证据的具体实体")
    assert_true({u["canonical_name"] for u in unclassified} == {"AR/VR", "材料"},
                "无关系实体和泛化实体应导出为未分类，不得猜测挂载")
    assert_true(any(e["is_evidence_fact"] == "true" for e in edges), "关系父子边必须标记证据事实")
    assert_true(validate_dynamic_tree(nodes, edges, context)["passed"], "关系优先树应通过2-4级校验")


def test_duplicate_source_entity_cannot_appear_twice_in_tree():
    from build_document_driven_template import validate_dynamic_tree

    nodes = [
        {"tree_node_id": "T1", "label": "显示产业链", "parent_id": "", "depth": 0,
         "category": "root", "source_entity_ids": "", "evidence_ids": ""},
        {"tree_node_id": "T2", "label": "新型显示器件", "parent_id": "T1", "depth": 1,
         "category": "product", "source_entity_ids": "CE1", "evidence_ids": ""},
        {"tree_node_id": "T3", "label": "新型显示器件重复", "parent_id": "T2", "depth": 2,
         "category": "product", "source_entity_ids": "CE1", "evidence_ids": ""},
    ]
    edges = [
        {"tree_edge_id": "E1", "parent_tree_node_id": "T1", "child_tree_node_id": "T2", "is_evidence_fact": "false"},
        {"tree_edge_id": "E2", "parent_tree_node_id": "T2", "child_tree_node_id": "T3", "is_evidence_fact": "false"},
    ]
    context = {"maps": {"by_id": {"CE1": {}}}, "evidence_universe": set(),
               "validation_rules": {"preferred_depth_min": 2}, "max_depth": 4}
    result = validate_dynamic_tree(nodes, edges, context)
    assert_true(any("duplicate_source_entity_id:CE1" in issue for issue in result["issues"]),
                "同一approved实体不得在树中重复出现")


def test_refinement_preserves_evidence_edge_metadata():
    from refine_hierarchy_tree import recompute_depths_and_edges

    nodes = [
        {"tree_node_id": "T1", "label": "显示产业链", "parent_id": "", "depth": 0, "sort_order": 0},
        {"tree_node_id": "T2", "label": "液晶面板", "parent_id": "T1", "depth": 1, "sort_order": 1},
        {"tree_node_id": "T3", "label": "玻璃基板", "parent_id": "T2", "depth": 2,
         "sort_order": 1, "evidence_ids": "EV1"},
    ]
    original_edges = [
        {"parent_tree_node_id": "T1", "child_tree_node_id": "T2", "source_from": "document_driven_rules",
         "is_evidence_fact": "false", "evidence_ids": "", "notes": "展示挂载"},
        {"parent_tree_node_id": "T2", "child_tree_node_id": "T3", "source_from": "approved_relation",
         "is_evidence_fact": "true", "evidence_ids": "EV1", "notes": "NR1明确输入关系"},
    ]
    _, edges = recompute_depths_and_edges(nodes, original_edges)
    factual = next(e for e in edges if e["child_tree_node_id"] == "T3")
    assert_true(factual["is_evidence_fact"] == "true" and factual["source_from"] == "approved_relation",
                "refinement重建边时不得丢失证据事实标记")
    assert_true(factual["evidence_ids"] == "EV1", "refinement必须保留原关系证据ID")


def main():
    tests = [
        test_p1_concrete_item_reaches_review,
        test_forbidden_label_rejected_at_any_depth,
        test_dynamic_depth_is_two_to_four,
        test_missing_validation_is_not_success,
        test_relation_cooccurrence_is_not_verified_as_supply,
        test_relation_endpoints_must_both_be_in_quote,
        test_part_of_direction_follows_child_to_parent,
        test_allowed_doc_ids_are_effective_filters,
        test_visual_review_notes_are_document_scoped,
        test_adjacent_pdf_lines_form_one_evidence_chunk,
        test_pdf_text_and_schema_alias_normalization,
        test_explicit_input_predicates_and_company_endpoint,
        test_forecast_filter_is_scoped_to_relation_quote,
        test_explicit_part_of_pattern_fallback,
        test_explicit_unit_input_pattern_preserves_source_endpoints,
        test_same_name_entity_types_are_resolved_before_tree_building,
        test_stage4_6_cannot_map_rejected_candidate_to_unrelated_relation,
        test_relation_first_tree_uses_only_evidence_connected_concrete_entities,
        test_duplicate_source_entity_cannot_appear_twice_in_tree,
        test_refinement_preserves_evidence_edge_metadata,
    ]
    for test in tests:
        test()
        print(f"✓ {test.__name__}")
    print(f"{len(tests)} accuracy guardrail tests passed")


if __name__ == "__main__":
    main()
