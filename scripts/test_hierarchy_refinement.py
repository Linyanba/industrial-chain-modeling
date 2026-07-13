#!/usr/bin/env python3
"""
hierarchy_refinement 单元测试。
覆盖：
- 封装测试作为父节点，封装/测试不得同层
- 代表企业分支删除
- 输出 refined_hierarchy_tree_*、fixes、quality report
"""
import argparse
import csv
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


NODE_FIELDS = [
    "tree_node_id", "label", "display_label", "parent_id", "level", "depth",
    "sort_order", "category", "node_type", "source_from", "source_entity_ids",
    "evidence_ids", "is_display_node", "is_schema_node", "notes",
]
EDGE_FIELDS = [
    "tree_edge_id", "parent_tree_node_id", "child_tree_node_id", "edge_type",
    "sort_order", "source_from", "is_evidence_fact", "evidence_ids", "notes",
]


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def make_node(tid, label, parent_id, depth, category):
    return {
        "tree_node_id": tid,
        "label": label,
        "display_label": label,
        "parent_id": parent_id,
        "level": depth,
        "depth": depth,
        "sort_order": 1,
        "category": category,
        "node_type": "display_node",
        "source_from": "test",
        "source_entity_ids": "",
        "evidence_ids": "",
        "is_display_node": "true",
        "is_schema_node": "true" if depth <= 1 else "false",
        "notes": "",
    }


def edge(eid, parent, child, sort):
    return {
        "tree_edge_id": eid,
        "parent_tree_node_id": parent,
        "child_tree_node_id": child,
        "edge_type": "DISPLAY_PARENT_OF",
        "sort_order": sort,
        "source_from": "test",
        "is_evidence_fact": "false",
        "evidence_ids": "",
        "notes": "",
    }


def test_packaging_testing_fix(project_root: Path):
    from refine_hierarchy_tree import refine_hierarchy_tree

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        nodes = [
            make_node("T0001", "半导体产业链", "", 0, "root"),
            make_node("T0002", "集成电路主链", "T0001", 1, "main_chain"),
            make_node("T0003", "封装测试", "T0002", 2, "industry_link"),
            make_node("T0004", "封装", "T0002", 2, "industry_link"),
            make_node("T0005", "测试", "T0002", 2, "industry_link"),
            make_node("T0006", "代表企业", "T0001", 1, "company_group"),
            make_node("T0007", "材料", "T0001", 1, "material_group"),
            make_node("T0008", "硅片", "T0007", 2, "material"),
            make_node("T0009", "光刻胶", "T0007", 2, "material"),
        ]
        edges = [
            edge("TE0001", "T0001", "T0002", 1),
            edge("TE0002", "T0002", "T0003", 1),
            edge("TE0003", "T0002", "T0004", 2),
            edge("TE0004", "T0002", "T0005", 3),
            edge("TE0005", "T0001", "T0006", 2),
            edge("TE0006", "T0001", "T0007", 3),
            edge("TE0007", "T0007", "T0008", 1),
            edge("TE0008", "T0007", "T0009", 2),
        ]
        write_csv(tmp / "hierarchy_tree_nodes.csv", nodes, NODE_FIELDS)
        write_csv(tmp / "hierarchy_tree_edges.csv", edges, EDGE_FIELDS)
        (tmp / "hierarchy_tree_data.json").write_text("{}", encoding="utf-8")

        result = refine_hierarchy_tree(
            project_root, tmp, tmp,
            config_overrides={"allow_llm_refinement": False, "min_tree_depth": 3},
        )
        assert result["passed"], result["validation"]
        refined = read_csv(tmp / "refined_hierarchy_tree_nodes.csv")
        labels = {n["label"] for n in refined}
        assert "代表企业" not in labels
        by_label = {n["label"]: n for n in refined}
        assert by_label["封装"]["parent_id"] == by_label["封装测试"]["tree_node_id"]
        assert by_label["测试"]["parent_id"] == by_label["封装测试"]["tree_node_id"]
        assert (tmp / "hierarchy_parent_child_fixes.csv").exists()
        assert (tmp / "hierarchy_quality_report.csv").exists()
        fixes = read_csv(tmp / "hierarchy_parent_child_fixes.csv")
        assert any(f["child_label"] == "封装" and f["new_parent_label"] == "封装测试" for f in fixes)
        print("  ✓ 封装测试父子修复、代表企业删除、refined输出生成")
        return result


def test_current_latest_tree(project_root: Path):
    from refine_hierarchy_tree import refine_hierarchy_tree

    latest = project_root / "rag" / "latest_final_hierarchy_tree_run.txt"
    if not latest.exists():
        print("  - 跳过当前树回归：latest_final_hierarchy_tree_run.txt不存在")
        return None
    source = Path(latest.read_text(encoding="utf-8").strip())
    if not (source / "hierarchy_tree_nodes.csv").exists():
        print("  - 跳过当前树回归：latest hierarchy_tree_nodes.csv不存在")
        return None
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name in ["hierarchy_tree_nodes.csv", "hierarchy_tree_edges.csv", "hierarchy_tree_data.json"]:
            src = source / name
            if src.exists():
                (tmp / name).write_bytes(src.read_bytes())
        result = refine_hierarchy_tree(
            project_root, tmp, tmp,
            config_overrides={"allow_llm_refinement": False, "min_tree_depth": 3},
        )
        assert result["passed"], result["validation"]
        assert result["max_depth"] >= 3
        labels = {n["label"] for n in result["nodes"]}
        assert "代表企业" not in labels
        print(f"  ✓ 当前最新树refinement回归通过: {result['original_node_count']} -> {result['refined_node_count']} nodes")
        return result


def main():
    parser = argparse.ArgumentParser(description="hierarchy_refinement测试")
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    project_root = Path(args.project_root)

    tests = [
        ("packaging_testing_fix", lambda: test_packaging_testing_fix(project_root)),
        ("current_latest_tree", lambda: test_current_latest_tree(project_root)),
    ]
    failures = []
    passed = 0
    print("=" * 60)
    print("hierarchy_refinement 测试")
    print("=" * 60)
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            failures.append((name, str(exc)))
            traceback.print_exc()
    print("\n" + "=" * 60)
    print(f"测试结果: {passed} 通过, {len(failures)} 失败")
    if failures:
        for name, err in failures:
            print(f"  ✗ {name}: {err}")
    print("=" * 60)
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    main()
