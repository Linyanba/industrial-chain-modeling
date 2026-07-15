#!/usr/bin/env python3
"""
产业链模板管理器：加载、验证模板，分类实体，构建层级树。
"""
import csv, json, logging
from pathlib import Path
from typing import Optional
import yaml

logger = logging.getLogger(__name__)
TEMPLATES_DIR = Path(__file__).parent.parent / "rag" / "templates"
REQUIRED_SCHEMA_FIELDS = [
    "template_id", "template_name", "root_label_default", "description",
    "first_level_nodes", "node_alias_rules", "entity_classification_rules",
    "tree_build_rules", "display_style", "required_outputs",
]
def list_available_templates() -> list[str]:
    return [f.stem for f in TEMPLATES_DIR.glob("*.yaml")]

def load_template(template_id: str) -> dict:
    available = list_available_templates()
    if template_id not in available:
        raise ValueError(f"未知模板 '{template_id}'。可用模板: {available}")
    path = TEMPLATES_DIR / f"{template_id}.yaml"
    with open(path, "r", encoding="utf-8") as f:
        tpl = yaml.safe_load(f)
    logger.info(f"加载模板: {template_id} ({tpl.get('template_name', '')})")
    return tpl

def validate_template_schema(template: dict) -> tuple[bool, list[str]]:
    errors = []
    for field in REQUIRED_SCHEMA_FIELDS:
        if field not in template:
            errors.append(f"缺少字段: {field}")
    if template.get("template_id") == "document_driven":
        forbidden = set(template.get("forbidden_default_first_level_nodes", []))
        defaults = {n.get("label", "") for n in template.get("first_level_nodes", [])}
        bad = forbidden & defaults
        if bad:
            errors.append(f"document_driven不应默认包含上中下游一级节点: {bad}")
    return len(errors) == 0, errors

def build_hierarchy_tree(template: dict, root_label: str) -> tuple[list[dict], list[dict]]:
    """Build tree nodes and edges from template structure."""
    if template.get("template_id") == "document_driven" and not template.get("first_level_nodes"):
        template = dict(template)
        template["first_level_nodes"] = template.get("fallback_display_schema_nodes", [])
    nodes = []
    edges = []
    nid = 0

    def add_node(label, parent_id, depth, category, sort_order, is_schema=True):
        nonlocal nid
        nid += 1
        tid = f"T{nid:04d}"
        nodes.append({
            "tree_node_id": tid, "label": label, "display_label": label,
            "parent_id": parent_id, "level": depth, "depth": depth,
            "sort_order": sort_order, "category": category,
            "node_type": "display_node", "source_from": "template_schema",
            "source_entity_ids": "", "evidence_ids": "",
            "is_display_node": "true",
            "is_schema_node": "true" if is_schema else "false",
            "notes": "模板展示节点" if is_schema else "",
        })
        return tid

    def add_edge(parent_tid, child_tid, sort_order):
        edges.append({
            "tree_edge_id": f"TE{len(edges)+1:04d}",
            "parent_tree_node_id": parent_tid,
            "child_tree_node_id": child_tid,
            "edge_type": "DISPLAY_PARENT_OF",
            "sort_order": sort_order,
            "source_from": "template_schema",
            "is_evidence_fact": "false",
            "evidence_ids": "",
            "notes": "展示层级边",
        })

    root_id = add_node(root_label, "", 0, "root", 0)

    def process_children(children, parent_id, depth, start_sort=1):
        for i, child in enumerate(children, start_sort):
            cid = add_node(child["label"], parent_id, depth, child.get("category", ""), i)
            add_edge(parent_id, cid, i)
            sub = child.get("children", [])
            if sub:
                process_children(sub, cid, depth + 1)

    process_children(template["first_level_nodes"], root_id, 1)
    logger.info(f"树构建: {len(nodes)} nodes, {len(edges)} edges")
    return nodes, edges

def render_tree_svg(nodes, edges, template, out_dir: Path):
    """Render SVG/HTML tree visualization."""
    style = template.get("display_style", {})
    outputs = template.get("required_outputs", {})
    title = outputs.get("title", "产业链多级结构图")
    prefix = outputs.get("svg_prefix", "industry_chain_tree")

    id_to_node = {n["tree_node_id"]: n for n in nodes}
    children_map = {}
    for e in edges:
        children_map.setdefault(e["parent_tree_node_id"], []).append(e["child_tree_node_id"])
    for pid in children_map:
        children_map[pid].sort(key=lambda cid: int(id_to_node[cid]["sort_order"]))

    root = [n for n in nodes if n["parent_id"] == ""][0]
    level_gap = style.get("level_gap_x", 180)
    node_h = 30
    pad_x = 16

    def subtree_h(nid):
        kids = children_map.get(nid, [])
        if not kids:
            return node_h + 8
        return sum(subtree_h(k) for k in kids) + (len(kids) - 1) * 4

    positions = {}
    def layout(nid, x, y_start, y_end):
        positions[nid] = (x, (y_start + y_end) / 2)
        kids = children_map.get(nid, [])
        if not kids:
            return
        total = sum(subtree_h(k) for k in kids) + (len(kids) - 1) * 4
        cy = y_start + (y_end - y_start - total) / 2
        for kid in kids:
            kh = subtree_h(kid)
            layout(kid, x + level_gap, cy, cy + kh)
            cy += kh + 4

    th = subtree_h(root["tree_node_id"])
    canvas_h = max(th + 80, 600)
    layout(root["tree_node_id"], 60, 40, canvas_h - 40)
    max_x = max(pos[0] for pos in positions.values()) + 200
    canvas_w = max(max_x + 60, 1000)

    font = style.get("font_family", "Microsoft YaHei")
    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w}" height="{canvas_h}" viewBox="0 0 {canvas_w} {canvas_h}">',
        f'<rect width="100%" height="100%" fill="{style.get("background","#FFFFFF")}"/>',
        f'<text x="{canvas_w/2}" y="25" text-anchor="middle" font-family="{font}" font-size="18" font-weight="bold" fill="#1B5E8C">{title}</text>',
    ]
    # Edges
    for e in edges:
        pid, cid = e["parent_tree_node_id"], e["child_tree_node_id"]
        if pid in positions and cid in positions:
            px, py = positions[pid]
            cx, cy = positions[cid]
            p_label = id_to_node[pid]["label"]
            sx = px + len(p_label) * 7 + pad_x
            mx = (sx + cx) / 2
            svg_lines.append(
                f'<path d="M{sx},{py} C{mx},{py} {mx},{cy} {cx},{cy}" '
                f'fill="none" stroke="{style.get("line_color","#7CB8E4")}" stroke-width="{style.get("line_width",2)}"/>')
    # Nodes
    rx = style.get("node_rx", 8)
    for n in nodes:
        nid = n["tree_node_id"]
        if nid not in positions:
            continue
        x, y = positions[nid]
        label = n["display_label"]
        depth = int(n["depth"])
        if depth == 0:
            fill = style.get("root_fill", "#1B5E8C")
        elif depth == 1:
            fill = style.get("level1_fill", "#3B82C4")
        else:
            fill = style.get("node_fill", "#4A90D9")
        tc = style.get("node_text_color", "#FFFFFF")
        fs = max(10, 16 - depth * 2)
        tw = len(label) * fs * 0.6 + pad_x * 2
        svg_lines.append(
            f'<rect x="{x}" y="{y-node_h/2}" width="{tw}" height="{node_h}" '
            f'rx="{rx}" ry="{rx}" fill="{fill}" stroke="{style.get("node_stroke","#2E6DAB")}" stroke-width="1"/>')
        svg_lines.append(
            f'<text x="{x+tw/2}" y="{y+5}" text-anchor="middle" '
            f'font-family="{font}" font-size="{fs}" fill="{tc}">{label}</text>')
    svg_lines.append('</svg>')
    svg_content = "\n".join(svg_lines)

    (out_dir / f"{prefix}.svg").write_text(svg_content, encoding="utf-8")
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{margin:20px;font-family:{font},sans-serif;background:#f8f9fa}}</style></head><body>
<h1 style="color:#1B5E8C;text-align:center">{title}</h1>
<div style="overflow-x:auto;text-align:center">{svg_content}</div>
<p style="color:#666;text-align:center;font-size:12px">注：本图为面向展示的多级产业链结构，非知识图谱网络。</p></body></html>"""
    (out_dir / f"{prefix}.html").write_text(html, encoding="utf-8")
    return svg_content

def render_tree_png(nodes, edges, template, out_dir: Path, dpi=300):
    """Render PNG using matplotlib."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    style = template.get("display_style", {})
    outputs = template.get("required_outputs", {})
    title = outputs.get("title", "产业链多级结构图")
    prefix = outputs.get("png_prefix", "industry_chain_tree")

    plt.rcParams["font.sans-serif"] = [style.get("font_family","Microsoft YaHei"), "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    id_to_node = {n["tree_node_id"]: n for n in nodes}
    children_map = {}
    for e in edges:
        children_map.setdefault(e["parent_tree_node_id"], []).append(e["child_tree_node_id"])
    for pid in children_map:
        children_map[pid].sort(key=lambda cid: int(id_to_node[cid]["sort_order"]))

    root = [n for n in nodes if n["parent_id"] == ""][0]
    level_gap = 3.0
    node_h = 0.5

    def subtree_h(nid):
        kids = children_map.get(nid, [])
        if not kids:
            return node_h + 0.15
        return sum(subtree_h(k) for k in kids) + (len(kids) - 1) * 0.08

    positions = {}
    def do_layout(nid, x, y_s, y_e):
        positions[nid] = (x, (y_s + y_e) / 2)
        kids = children_map.get(nid, [])
        if not kids:
            return
        total = sum(subtree_h(k) for k in kids) + (len(kids) - 1) * 0.08
        cy = y_s + (y_e - y_s - total) / 2
        for kid in kids:
            kh = subtree_h(kid)
            do_layout(kid, x + level_gap, cy, cy + kh)
            cy += kh + 0.08

    th = subtree_h(root["tree_node_id"])
    canvas_h = max(th + 1, 8)
    do_layout(root["tree_node_id"], 0.5, 0.5, canvas_h - 0.5)
    max_x = max(p[0] for p in positions.values()) + 3.5

    fig, ax = plt.subplots(figsize=(max_x, canvas_h), facecolor="white")
    ax.set_facecolor("white")
    ax.set_xlim(-0.5, max_x)
    ax.set_ylim(0, canvas_h)
    ax.axis("off")
    ax.set_title(title, fontsize=14, fontweight="bold", color="#1B5E8C", pad=10)

    for e in edges:
        pid, cid = e["parent_tree_node_id"], e["child_tree_node_id"]
        if pid in positions and cid in positions:
            px, py = positions[pid]
            cx, cy = positions[cid]
            p_label = id_to_node[pid]["label"]
            sx = px + len(p_label) * 0.12 + 0.3
            mx = (sx + cx) / 2
            ax.plot([sx, mx, mx, cx], [py, py, cy, cy],
                    color=style.get("line_color", "#7CB8E4"), lw=1.5, solid_capstyle="round")

    for n in nodes:
        nid = n["tree_node_id"]
        if nid not in positions:
            continue
        x, y = positions[nid]
        label = n["display_label"]
        depth = int(n["depth"])
        if depth == 0:
            fc = style.get("root_fill", "#1B5E8C")
        elif depth == 1:
            fc = style.get("level1_fill", "#3B82C4")
        else:
            fc = style.get("node_fill", "#4A90D9")
        tc = style.get("node_text_color", "#FFFFFF")
        fs = max(8, 11 - depth)
        tw = len(label) * 0.12 + 0.5
        bbox = FancyBboxPatch((x, y - 0.2), tw, 0.4,
                              boxstyle="round,pad=0.05", fc=fc,
                              ec=style.get("node_stroke", "#2E6DAB"), lw=0.8)
        ax.add_patch(bbox)
        ax.text(x + tw / 2, y, label, ha="center", va="center", fontsize=fs, color=tc, fontweight="bold")

    plt.tight_layout()
    fig.savefig(str(out_dir / f"{prefix}.png"), dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)

def auto_detect_template(pdf_path: Path, root_label: str = "") -> str:
    """Simple rule-based template detection."""
    keywords = {"半导体", "集成电路", "晶圆", "封装", "EDA", "光刻机", "芯片"}
    text = pdf_path.stem + " " + root_label
    if any(kw in text for kw in keywords):
        return "semiconductor"
    return "document_driven"
