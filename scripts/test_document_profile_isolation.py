#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Regression checks for generic/specialty document-rule isolation."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from document_profile_manager import document_rules, resolve_document_profile


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    generic = resolve_document_profile(ROOT, hints=("新能源汽车产业白皮书.pdf",))
    check(generic["profile_id"] == "generic", "非半导体报告必须使用 generic")
    check(document_rules(generic)["diagram_restricted_pages"] == [], "generic 不得有固定受限页")
    check(document_rules(generic)["header_footer_markers"] == [], "generic 不得继承专用页眉")

    broad_semiconductor = resolve_document_profile(ROOT, hints=("半导体行业研究报告.pdf",))
    check(
        broad_semiconductor["profile_id"] == "generic",
        "宽泛行业词不得触发特定报告的固定页码规则",
    )

    specialty = resolve_document_profile(ROOT, hints=("中国半导体白皮书.pdf",))
    rules = document_rules(specialty)
    check(specialty["profile_id"] == "china_semiconductor_whitepaper", "专用报告应匹配专用档案")
    check(rules["diagram_restricted_pages"] == [9, 14], "专用固定页规则应保留")
    check("BAIN" in rules["header_footer_markers"], "专用页眉规则应保留")

    forced_generic = resolve_document_profile(
        ROOT, hints=("中国半导体白皮书.pdf",), explicit="generic"
    )
    check(forced_generic["profile_id"] == "generic", "显式 generic 必须覆盖自动识别")
    check(document_rules(forced_generic)["diagram_restricted_pages"] == [], "强制 generic 后固定页必须为空")

    shared_files = [
        ROOT / "scripts" / "stage1_document_audit.py",
        ROOT / "scripts" / "stage2_build_evidence.py",
        ROOT / "scripts" / "stage2_ocr_repair_evidence.py",
        ROOT / "scripts" / "stage3c_rag_extract_candidates.py",
    ]
    specialty_literals = ("中国半导体白皮书", "BAIN", "{9, 14}")
    for path in shared_files:
        text = path.read_text(encoding="utf-8")
        for literal in specialty_literals:
            check(literal not in text, f"共享脚本仍含专用字面量: {path.name}: {literal}")

    extraction_cfg = yaml.safe_load(
        (ROOT / "rag" / "config" / "rag_extraction_config.yaml").read_text(encoding="utf-8")
    )
    check(extraction_cfg.get("diagram_restricted_pages") == [], "共享抽取配置固定页必须为空")

    final_cfg_text = (ROOT / "rag" / "config" / "final_hierarchy_tree_config.yaml").read_text(encoding="utf-8")
    check("半导体产业链" not in final_cfg_text, "共享最终配置不得包含半导体静态树")
    check("hierarchy_tree:" not in final_cfg_text, "共享最终配置不得保存产业专用静态树")

    one_click_text = (ROOT / "scripts" / "run_industry_chain_one_click.py").read_text(encoding="utf-8")
    check("stage5_graph_readiness_check.py" not in one_click_text, "通用一键流程不得调用专用 Stage5")
    check("stage5_5_graph_structure_patch.py" not in one_click_text, "通用一键流程不得调用专用 Stage5.5")
    check("stage6_build_and_export_graph.py" not in one_click_text, "通用一键流程不得调用专用 Stage6")
    for name in (
        "stage5_graph_readiness_check.py",
        "stage5_5_graph_structure_patch.py",
        "stage6_build_and_export_graph.py",
    ):
        specialty_script = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        check(
            '"--specialization", required=True' in specialty_script,
            f"旧版专用阶段缺少显式运行门: {name}",
        )

    print("PASS: document profile isolation (generic + specialty + explicit override)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
