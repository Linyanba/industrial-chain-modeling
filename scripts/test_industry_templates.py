#!/usr/bin/env python3
"""
模板系统测试脚本：验证 semiconductor/generic 模板正确性。
"""
import argparse, sys, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def test_load_semiconductor():
    """测试加载 semiconductor.yaml 成功"""
    from industry_template_manager import load_template
    tpl = load_template("semiconductor")
    assert tpl["template_id"] == "semiconductor", "template_id 不匹配"
    assert tpl["root_label_default"] == "半导体产业链"
    print("  ✓ 加载 semiconductor.yaml 成功")
    return tpl


def test_load_generic():
    """测试加载 generic.yaml 成功"""
    from industry_template_manager import load_template
    tpl = load_template("generic")
    assert tpl["template_id"] == "generic", "template_id 不匹配"
    assert tpl["root_label_default"] == "产业链"
    print("  ✓ 加载 generic.yaml 成功")
    return tpl


def test_schema_validation_semiconductor(tpl):
    """测试 semiconductor 模板 schema 校验通过"""
    from industry_template_manager import validate_template_schema
    ok, errors = validate_template_schema(tpl)
    assert ok, f"semiconductor schema 校验失败: {errors}"
    print("  ✓ semiconductor 模板 schema 校验通过")


def test_schema_validation_generic(tpl):
    """测试 generic 模板 schema 校验通过"""
    from industry_template_manager import validate_template_schema
    ok, errors = validate_template_schema(tpl)
    assert ok, f"generic schema 校验失败: {errors}"
    print("  ✓ generic 模板 schema 校验通过")


def test_generic_no_semiconductor_defaults(tpl):
    """测试 generic 模板不含半导体默认节点"""
    from industry_template_manager import SEMICONDUCTOR_DEFAULT_NODES, _collect_all_labels
    all_labels = _collect_all_labels(tpl)
    bad = all_labels & SEMICONDUCTOR_DEFAULT_NODES
    assert len(bad) == 0, f"generic 模板包含半导体默认节点: {bad}"
    print("  ✓ generic 模板不含半导体默认节点")


def test_semiconductor_structure(tpl):
    """测试 semiconductor 模板输出结构包含集成电路主链"""
    from industry_template_manager import build_hierarchy_tree
    nodes, edges = build_hierarchy_tree(tpl, "半导体产业链")
    labels = {n["label"] for n in nodes}
    assert "集成电路主链" in labels, "缺少'集成电路主链'"
    assert "芯片设计" in labels, "缺少'芯片设计'"
    assert "晶圆制造" in labels, "缺少'晶圆制造'"
    assert "封装测试" in labels, "缺少'封装测试'"
    print(f"  ✓ semiconductor 模板输出包含'集成电路主链' ({len(nodes)} nodes, {len(edges)} edges)")


def test_generic_structure(tpl):
    """测试 generic 模板输出结构包含上游/中游/下游/支撑体系，且不包含代表企业"""
    from industry_template_manager import build_hierarchy_tree
    nodes, edges = build_hierarchy_tree(tpl, "产业链")
    labels = {n["label"] for n in nodes}
    required = {"上游", "中游", "下游", "支撑体系"}
    missing = required - labels
    assert len(missing) == 0, f"generic 模板缺少一级节点: {missing}"
    assert "代表企业" not in labels, "generic 模板不应包含代表企业"
    print(f"  ✓ generic 模板输出包含上游/中游/下游/支撑体系且不含代表企业 ({len(nodes)} nodes, {len(edges)} edges)")


def test_unknown_template_error():
    """测试未知模板 ID 会报错"""
    from industry_template_manager import load_template
    try:
        load_template("unknown_template_xyz")
        assert False, "应该抛出 ValueError"
    except ValueError as e:
        assert "未知模板" in str(e)
        print(f"  ✓ 未知模板 ID 正确报错: {e}")


def test_default_template_is_generic():
    """测试默认模板为 document_driven（通过 argparse 默认值验证）"""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--industry-template", default="document_driven")
    args = parser.parse_args([])
    assert args.industry_template == "document_driven", f"默认模板应为 document_driven，实际为 {args.industry_template}"
    print("  ✓ 默认模板为 document_driven")


def test_render_svg_semiconductor(tpl):
    """测试 semiconductor 模板 SVG 渲染"""
    import tempfile
    from industry_template_manager import build_hierarchy_tree, render_tree_svg
    nodes, edges = build_hierarchy_tree(tpl, "半导体产业链")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        svg = render_tree_svg(nodes, edges, tpl, out)
        svg_file = out / "semiconductor_industry_chain_tree.svg"
        assert svg_file.exists(), "SVG 文件未生成"
        content = svg_file.read_text(encoding="utf-8")
        assert "半导体产业链" in content
        print(f"  ✓ semiconductor SVG 渲染成功 ({svg_file.stat().st_size} bytes)")


def test_render_svg_generic(tpl):
    """测试 generic 模板 SVG 渲染"""
    import tempfile
    from industry_template_manager import build_hierarchy_tree, render_tree_svg
    nodes, edges = build_hierarchy_tree(tpl, "产业链")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        svg = render_tree_svg(nodes, edges, tpl, out)
        svg_file = out / "industry_chain_tree.svg"
        assert svg_file.exists(), "SVG 文件未生成"
        content = svg_file.read_text(encoding="utf-8")
        assert "产业链" in content
        # Verify no semiconductor defaults in SVG
        from industry_template_manager import SEMICONDUCTOR_DEFAULT_NODES
        for node in SEMICONDUCTOR_DEFAULT_NODES:
            assert node not in content, f"generic SVG 包含半导体节点: {node}"
        print(f"  ✓ generic SVG 渲染成功且不含半导体默认节点 ({svg_file.stat().st_size} bytes)")


def test_auto_detect():
    """测试自动检测模板功能"""
    from industry_template_manager import auto_detect_template
    # Semiconductor keywords
    assert auto_detect_template(Path("中国半导体白皮书.pdf")) == "semiconductor"
    assert auto_detect_template(Path("集成电路产业报告.pdf")) == "semiconductor"
    # Non-semiconductor defaults to document_driven
    assert auto_detect_template(Path("汽车产业链分析.pdf")) == "document_driven"
    assert auto_detect_template(Path("新能源产业白皮书.pdf")) == "document_driven"
    print("  ✓ 自动检测模板功能正常")


def main():
    parser = argparse.ArgumentParser(description="模板系统测试")
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()

    print("=" * 50)
    print("产业链模板系统测试")
    print("=" * 50)

    tests_passed = 0
    tests_failed = 0
    failures = []

    test_funcs = [
        ("加载 semiconductor.yaml", lambda: test_load_semiconductor()),
        ("加载 generic.yaml", lambda: test_load_generic()),
        ("semiconductor schema 校验", lambda: test_schema_validation_semiconductor(semi_tpl)),
        ("generic schema 校验", lambda: test_schema_validation_generic(gen_tpl)),
        ("generic 不含半导体默认节点", lambda: test_generic_no_semiconductor_defaults(gen_tpl)),
        ("semiconductor 输出结构", lambda: test_semiconductor_structure(semi_tpl)),
        ("generic 输出结构", lambda: test_generic_structure(gen_tpl)),
        ("未知模板报错", lambda: test_unknown_template_error()),
        ("默认模板为 document_driven", lambda: test_default_template_is_generic()),
        ("semiconductor SVG 渲染", lambda: test_render_svg_semiconductor(semi_tpl)),
        ("generic SVG 渲染", lambda: test_render_svg_generic(gen_tpl)),
        ("自动检测模板", lambda: test_auto_detect()),
    ]

    # Load templates first
    try:
        semi_tpl = test_load_semiconductor()
        tests_passed += 1
    except Exception as e:
        tests_failed += 1
        failures.append(("加载 semiconductor.yaml", str(e)))
        semi_tpl = None
        traceback.print_exc()

    try:
        gen_tpl = test_load_generic()
        tests_passed += 1
    except Exception as e:
        tests_failed += 1
        failures.append(("加载 generic.yaml", str(e)))
        gen_tpl = None
        traceback.print_exc()

    if not semi_tpl or not gen_tpl:
        print(f"\n基础加载失败，无法继续测试")
        sys.exit(1)

    # Run remaining tests
    remaining_tests = [
        ("semiconductor schema 校验", lambda: test_schema_validation_semiconductor(semi_tpl)),
        ("generic schema 校验", lambda: test_schema_validation_generic(gen_tpl)),
        ("generic 不含半导体默认节点", lambda: test_generic_no_semiconductor_defaults(gen_tpl)),
        ("semiconductor 输出结构", lambda: test_semiconductor_structure(semi_tpl)),
        ("generic 输出结构", lambda: test_generic_structure(gen_tpl)),
        ("未知模板报错", lambda: test_unknown_template_error()),
        ("默认模板为 document_driven", lambda: test_default_template_is_generic()),
        ("semiconductor SVG 渲染", lambda: test_render_svg_semiconductor(semi_tpl)),
        ("generic SVG 渲染", lambda: test_render_svg_generic(gen_tpl)),
        ("自动检测模板", lambda: test_auto_detect()),
    ]

    for name, fn in remaining_tests:
        try:
            fn()
            tests_passed += 1
        except Exception as e:
            tests_failed += 1
            failures.append((name, str(e)))
            traceback.print_exc()

    print("\n" + "=" * 50)
    print(f"测试结果: {tests_passed} 通过, {tests_failed} 失败")
    if failures:
        print("\n失败项:")
        for name, err in failures:
            print(f"  ✗ {name}: {err}")
    print("=" * 50)

    sys.exit(0 if tests_failed == 0 else 1)


if __name__ == "__main__":
    main()
