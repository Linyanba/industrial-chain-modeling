#!/usr/bin/env python3
"""
document_driven 模板测试：
- 两个有效模板均可加载并通过基础schema校验
- document_driven 默认不输出“上游/中游/下游”
- document_driven 不输出“代表企业”
- semiconductor 固定模板不受影响
- 若已有approved数据，则动态树可生成debug与质量报告
"""
import argparse
import json
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def test_load_and_validate_templates():
    from industry_template_manager import load_template, validate_template_schema

    templates = {}
    for tid in ["document_driven", "semiconductor"]:
        tpl = load_template(tid)
        ok, errors = validate_template_schema(tpl)
        assert_true(ok, f"{tid} schema校验失败: {errors}")
        templates[tid] = tpl
        print(f"  ✓ {tid}.yaml 加载并通过schema校验")
    return templates


def test_document_driven_no_up_mid_down(tpl):
    forbidden = {"上游", "中游", "下游"}
    defaults = {n.get("label", "") for n in tpl.get("first_level_nodes", [])}
    fallback = {n.get("label", "") for n in tpl.get("fallback_display_schema_nodes", [])}
    assert_true(not (defaults & forbidden), f"document_driven默认一级节点包含禁用项: {defaults & forbidden}")
    assert_true(not (fallback & forbidden), f"document_driven fallback展示分类包含禁用项: {fallback & forbidden}")
    print("  ✓ document_driven 默认不输出上游/中游/下游")


def test_no_representative_company_branch(tpl):
    labels = {n.get("label", "") for n in tpl.get("first_level_nodes", [])}
    labels |= {n.get("label", "") for n in tpl.get("fallback_display_schema_nodes", [])}
    assert_true("代表企业" not in labels, "模板不应包含代表企业分支")
    assert_true("company" in set(tpl.get("excluded_tree_entity_types", [])), "模板应配置排除company实体进入树")
    print("  ✓ 模板已排除代表企业/company树节点")


def test_semiconductor_unchanged(tpl):
    from industry_template_manager import build_hierarchy_tree

    nodes, edges = build_hierarchy_tree(tpl, "半导体产业链")
    labels = {n["label"] for n in nodes}
    required = {"集成电路主链", "材料", "设备", "技术工具", "晶圆制造", "封装测试"}
    missing = required - labels
    assert_true(not missing, f"semiconductor模板缺少既有关键节点: {missing}")
    assert_true(len(nodes) >= 10 and len(edges) >= 9, "semiconductor模板输出规模异常")
    print(f"  ✓ semiconductor 模板不受影响 ({len(nodes)} nodes, {len(edges)} edges)")


def test_document_driven_dynamic_build(project_root: Path, tpl):
    from build_document_driven_template import build_document_driven_tree

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        result = build_document_driven_tree(project_root, tpl, "半导体产业链", out_dir, allow_llm=False)
        labels = set(result["selected_first_level_nodes"])
        forbidden = {"上游", "中游", "下游"}
        assert_true(result["validation"]["passed"], f"动态树校验失败: {result['validation']}")
        assert_true(not (labels & forbidden), f"动态树一级节点包含禁用项: {labels & forbidden}")
        assert_true("代表企业" not in labels, "动态树一级节点不应包含代表企业")
        node_labels = {n["label"] for n in result["nodes"]}
        assert_true("代表企业" not in node_labels, "动态树不应包含代表企业节点")
        assert_true((out_dir / "dynamic_template_debug.json").exists(), "未生成dynamic_template_debug.json")
        assert_true((out_dir / "template_quality_report.csv").exists(), "未生成template_quality_report.csv")
        assert_true((out_dir / "template_application_report.md").exists(), "未生成template_application_report.md")
        print(f"  ✓ document_driven 动态构建通过，一级节点: {', '.join(result['selected_first_level_nodes'])}")
        return result


def test_auto_detect_defaults_to_document_driven():
    from industry_template_manager import auto_detect_template

    assert_true(auto_detect_template(Path("新能源汽车白皮书.pdf")) == "document_driven", "非半导体自动检测应返回document_driven")
    assert_true(auto_detect_template(Path("中国半导体白皮书.pdf")) == "semiconductor", "半导体自动检测应仍返回semiconductor")
    print("  ✓ auto_detect 非半导体默认返回 document_driven，半导体仍返回 semiconductor")


def main():
    parser = argparse.ArgumentParser(description="document_driven模板测试")
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    project_root = Path(args.project_root)

    print("=" * 60)
    print("document_driven 模板测试")
    print("=" * 60)

    tests = []
    failures = []
    try:
        templates = test_load_and_validate_templates()
        tests.append("load_and_validate_templates")
    except Exception as exc:
        traceback.print_exc()
        print(f"  ✗ 模板加载/校验失败: {exc}")
        sys.exit(1)

    checks = [
        ("document_driven_no_up_mid_down", lambda: test_document_driven_no_up_mid_down(templates["document_driven"])),
        ("document_driven_no_representative_company", lambda: test_no_representative_company_branch(templates["document_driven"])),
        ("semiconductor_unchanged", lambda: test_semiconductor_unchanged(templates["semiconductor"])),
        ("auto_detect", test_auto_detect_defaults_to_document_driven),
        ("document_driven_dynamic_build", lambda: test_document_driven_dynamic_build(project_root, templates["document_driven"])),
    ]

    for name, fn in checks:
        try:
            fn()
            tests.append(name)
        except Exception as exc:
            failures.append((name, str(exc)))
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"测试结果: {len(tests)} 通过, {len(failures)} 失败")
    if failures:
        for name, err in failures:
            print(f"  ✗ {name}: {err}")
    print("=" * 60)
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    main()
