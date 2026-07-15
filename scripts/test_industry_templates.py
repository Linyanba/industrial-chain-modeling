#!/usr/bin/env python3
"""模板系统回归：通用 document_driven 与显式 semiconductor 模板。"""
import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="产业链模板系统测试")
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()

    from industry_template_manager import (
        auto_detect_template,
        build_hierarchy_tree,
        list_available_templates,
        load_template,
        render_tree_svg,
        validate_template_schema,
    )

    available = set(list_available_templates())
    check(available == {"document_driven", "semiconductor"}, f"模板集合异常: {available}")

    document_driven = load_template("document_driven")
    semiconductor = load_template("semiconductor")
    for template in (document_driven, semiconductor):
        ok, errors = validate_template_schema(template)
        check(ok, f"{template.get('template_id')} schema失败: {errors}")

    nodes, edges = build_hierarchy_tree(semiconductor, "半导体产业链")
    labels = {node["label"] for node in nodes}
    check("集成电路主链" in labels, "semiconductor模板缺少集成电路主链")
    check(not any(token in label for label in labels for token in ("上游", "中游", "下游", "上下游")),
          "显式半导体模板不得包含上中下游节点")
    check(len(nodes) == len(edges) + 1, "semiconductor模板不是单根树")

    check(auto_detect_template(Path("新能源汽车白皮书.pdf")) == "document_driven",
          "任意行业默认应使用document_driven")
    check(auto_detect_template(Path("中国半导体白皮书.pdf")) == "semiconductor",
          "半导体报告显式自动检测应保留专用模板")

    try:
        load_template("generic")
    except ValueError:
        pass
    else:
        raise AssertionError("已删除的generic树模板不应继续可加载")

    with tempfile.TemporaryDirectory() as tmp:
        render_tree_svg(nodes, edges, semiconductor, Path(tmp))
        prefix = semiconductor.get("required_outputs", {}).get("svg_prefix", "industry_chain_tree")
        check((Path(tmp) / f"{prefix}.svg").exists(), "SVG渲染失败")
        check((Path(tmp) / f"{prefix}.html").exists(), "HTML渲染失败")

    print("PASS: document_driven + semiconductor template system")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
