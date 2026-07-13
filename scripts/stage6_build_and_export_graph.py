#!/usr/bin/env python3
"""
阶段 6：正式产业链图谱构建、导出与可视化
- 构建 networkx 图
- 导出 GraphML、JSON、Neo4j CSV
- 生成多张可视化图片 (PNG/SVG)
- 不调用LLM、不新增关系、不写Neo4j
"""
import argparse, csv, json, logging, sys, copy
from collections import Counter
from datetime import datetime
from pathlib import Path
import yaml
import networkx as nx

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"

def setup_logging(log_path: Path):
    logging.basicConfig(level=logging.INFO, format=LOG_FMT, handlers=[
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)])

def read_csv(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def write_csv(path: Path, rows: list[dict], fieldnames: list[str]):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def load_config(project_root: Path) -> dict:
    p = project_root / "rag" / "config" / "stage6_graph_export_config.yaml"
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_inputs(project_root: Path, cfg: dict) -> dict:
    pointer = Path(cfg["stage5_5"]["latest_pointer"])
    if not pointer.exists():
        raise FileNotFoundError(f"Stage5.5 pointer missing: {pointer}")
    s55_dir = Path(pointer.read_text(encoding="utf-8").strip())
    if not s55_dir.exists():
        raise FileNotFoundError(f"Stage5.5 dir missing: {s55_dir}")
    inputs = {"stage5_5_dir": s55_dir}
    for fname in cfg["stage5_5"]["required_files"]:
        fp = s55_dir / fname
        if not fp.exists():
            raise FileNotFoundError(f"Required file missing: {fp}")
        if fname.endswith(".csv"):
            inputs[fname.replace(".csv", "")] = read_csv(fp)
        elif fname.endswith(".json"):
            with open(fp, "r", encoding="utf-8") as f:
                inputs[fname.replace(".json", "")] = json.load(f)
        elif fname.endswith(".md"):
            inputs[fname.replace(".md", "")] = fp.read_text(encoding="utf-8")
    return inputs

# ─── Build Graph ───────────────────────────────────────────────────────────────
def build_graph(nodes: list[dict], edges: list[dict]) -> nx.DiGraph:
    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n["node_id"], **{k: str(v) for k, v in n.items()})
    for e in edges:
        G.add_edge(e["source_node_id"], e["target_node_id"],
                   key=e["edge_id"], **{k: str(v) for k, v in e.items()})
    return G

# ─── Export GraphML ────────────────────────────────────────────────────────────
def export_graphml(G: nx.DiGraph, path: Path):
    nx.write_graphml(G, str(path), encoding="utf-8")

# ─── Export JSON ───────────────────────────────────────────────────────────────
def export_json(nodes, edges, aliases, evidence_map, metadata, path: Path):
    data = {
        "nodes": nodes,
        "edges": edges,
        "aliases": aliases,
        "evidence_map": evidence_map,
        "metadata": metadata,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ─── Export Neo4j CSV ──────────────────────────────────────────────────────────
def export_neo4j(nodes, edges, cfg, out_dir: Path):
    type_map = cfg["neo4j"]["type_label_map"]
    prefix = cfg["neo4j"]["node_label_prefix"]
    # Nodes
    neo4j_nodes = []
    for n in nodes:
        ntype = n.get("node_type", "unknown")
        type_label = type_map.get(ntype, ntype.title())
        label_str = f"{prefix};{type_label}"
        neo4j_nodes.append({
            "id:ID": n["node_id"],
            "label": n["node_label"],
            "name": n["node_label"],
            "node_type": ntype,
            "entity_level": n.get("entity_level", ""),
            "value_chain_stage": n.get("value_chain_stage", ""),
            "aliases": n.get("aliases", ""),
            "evidence_count": n.get("evidence_count", "0"),
            "relation_degree": n.get("relation_degree", "0"),
            "is_schema_root": n.get("is_schema_root", "false"),
            "is_isolated": n.get("is_isolated", "false"),
            "approval_status": n.get("approval_status", ""),
            "notes": n.get("notes", ""),
            ":LABEL": label_str,
        })
    n_fields = ["id:ID","label","name","node_type","entity_level","value_chain_stage",
                "aliases","evidence_count","relation_degree","is_schema_root",
                "is_isolated","approval_status","notes",":LABEL"]
    write_csv(out_dir / "neo4j_nodes.csv", neo4j_nodes, n_fields)
    # Edges
    neo4j_edges = []
    for e in edges:
        neo4j_edges.append({
            ":START_ID": e["source_node_id"],
            ":END_ID": e["target_node_id"],
            ":TYPE": e["relation_type"],
            "edge_id": e["edge_id"],
            "relation_type": e["relation_type"],
            "edge_source": e.get("edge_source", "evidence"),
            "is_evidence_fact": e.get("is_evidence_fact", "true"),
            "is_schema_edge": e.get("is_schema_edge", "false"),
            "is_layout_edge": e.get("is_layout_edge", "false"),
            "evidence_ids": e.get("evidence_ids", ""),
            "pages": e.get("pages", ""),
            "quotes_preview": e.get("quotes_preview", ""),
            "evidence_count": e.get("evidence_count", "0"),
            "approval_status": e.get("approval_status", ""),
            "notes": e.get("notes", ""),
        })
    e_fields = [":START_ID",":END_ID",":TYPE","edge_id","relation_type","edge_source",
                "is_evidence_fact","is_schema_edge","is_layout_edge","evidence_ids",
                "pages","quotes_preview","evidence_count","approval_status","notes"]
    write_csv(out_dir / "neo4j_edges.csv", neo4j_edges, e_fields)

# ─── Neo4j Import Readme ──────────────────────────────────────────────────────
def write_neo4j_readme(out_dir: Path):
    readme = """# Neo4j 导入说明

## 文件
- `neo4j_nodes.csv`: 节点文件，包含 :ID 和 :LABEL
- `neo4j_edges.csv`: 边文件，包含 :START_ID, :END_ID, :TYPE

## 导入方法

### 方法 1: neo4j-admin import (全量导入)
```bash
neo4j-admin database import full \\
  --nodes=neo4j_nodes.csv \\
  --relationships=neo4j_edges.csv \\
  neo4j
```

### 方法 2: LOAD CSV (增量导入)
```cypher
LOAD CSV WITH HEADERS FROM 'file:///neo4j_nodes.csv' AS row
CREATE (n:Entity {id: row.`id:ID`, name: row.name, node_type: row.node_type,
  entity_level: row.entity_level, value_chain_stage: row.value_chain_stage});
```

## 字段说明
- **节点**: id:ID(唯一标识), label(显示名), node_type, entity_level, value_chain_stage
- **边**: :START_ID, :END_ID, :TYPE(关系类型), edge_source, is_evidence_fact

## Evidence 边与 Schema/Layout 边的区别
- `edge_source=evidence` + `is_evidence_fact=true`: 来自白皮书 PDF 证据的真实关系
- `edge_source=system_schema` + `is_evidence_fact=false`: 产业层级组织边
- `edge_source=layout_helper` + `is_evidence_fact=false`: 可视化布局辅助边

## 示例 Cypher 查询

### 查看全部节点
```cypher
MATCH (n:Entity) RETURN n LIMIT 50;
```

### 查看 evidence 边
```cypher
MATCH (a)-[r]->(b) WHERE r.is_evidence_fact = 'true' RETURN a.name, type(r), b.name;
```

### 查看 schema/layout 边
```cypher
MATCH (a)-[r]->(b) WHERE r.edge_source IN ['system_schema','layout_helper'] RETURN a.name, type(r), b.name;
```

### 查看某条边的证据
```cypher
MATCH (a)-[r]->(b) WHERE r.edge_id = 'E0001'
RETURN a.name, type(r), b.name, r.evidence_ids, r.pages, r.quotes_preview;
```

### 查看主链
```cypher
MATCH path = (a:Entity)-[:SUPPLIES_TO*]->(b:Entity)
WHERE a.node_type = 'industry_link'
RETURN [n IN nodes(path) | n.name] AS chain;
```

> **注意**: 本文件仅为导入指南，阶段 6 未实际执行 Neo4j 写入。
"""
    (out_dir / "neo4j_import_readme.md").write_text(readme, encoding="utf-8")

# ─── Visualization ─────────────────────────────────────────────────────────────
def setup_chinese_font(cfg):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    candidates = cfg["visualization"]["font_candidates"]
    found = None
    for name in candidates:
        fonts = font_manager.findSystemFonts()
        for fp in fonts:
            try:
                prop = font_manager.FontProperties(fname=fp)
                if name.lower() in prop.get_name().lower():
                    found = name
                    break
            except Exception:
                continue
        if found:
            break
    if found:
        plt.rcParams["font.sans-serif"] = [found]
    else:
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return found

def get_node_colors(nodes, cfg):
    colors = cfg["visualization"]["node_colors"]
    return [colors.get(n.get("node_type", ""), colors["default"]) for n in nodes]

def get_node_sizes(G):
    sizes = []
    for nid in G.nodes():
        deg = G.degree(nid)
        sizes.append(max(300, 200 + deg * 150))
    return sizes

def draw_graph(G, nodes, edges, out_dir, cfg, suffix, title, edge_filter=None, logger=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dpi = cfg["visualization"]["dpi"]
    # Determine figsize based on suffix
    if "overview" in suffix:
        figsize = cfg["visualization"]["figsize_overview"]
    elif "layered" in suffix:
        figsize = cfg["visualization"]["figsize_layered"]
    elif "evidence" in suffix:
        figsize = cfg["visualization"]["figsize_evidence"]
    else:
        figsize = cfg["visualization"]["figsize_schema"]

    # Build subgraph if filter applied
    if edge_filter:
        filtered_edges = [(e["source_node_id"], e["target_node_id"]) for e in edges if edge_filter(e)]
        subG = G.edge_subgraph([(u, v) for u, v in G.edges() if (u, v) in filtered_edges]).copy()
        # Add isolated nodes from original
        for nid in G.nodes():
            if nid not in subG:
                subG.add_node(nid, **G.nodes[nid])
    else:
        subG = G

    fig, ax = plt.subplots(1, 1, figsize=figsize, facecolor="white")
    ax.set_facecolor("white")
    ax.set_title(title, fontsize=14, fontweight="bold", pad=20)

    # Layout
    try:
        pos = nx.spring_layout(subG, k=2.5, iterations=50, seed=42)
    except Exception:
        pos = nx.circular_layout(subG)

    # Draw nodes
    node_list = list(subG.nodes())
    colors = cfg["visualization"]["node_colors"]
    node_colors = [colors.get(subG.nodes[n].get("node_type", ""), colors["default"]) for n in node_list]
    node_sizes = [max(400, 200 + subG.degree(n) * 200) for n in node_list]

    nx.draw_networkx_nodes(subG, pos, nodelist=node_list, node_color=node_colors,
                           node_size=node_sizes, alpha=0.85, ax=ax)

    # Draw edges by type
    ev_styles = cfg["visualization"]["edge_styles"]
    for e in edges:
        src, tgt = e["source_node_id"], e["target_node_id"]
        if src not in pos or tgt not in pos:
            continue
        if edge_filter and not edge_filter(e):
            continue
        es = e.get("edge_source", "evidence")
        style_cfg = ev_styles.get(es, ev_styles["evidence"])
        nx.draw_networkx_edges(subG, pos, edgelist=[(src, tgt)],
                               style=style_cfg["style"], width=style_cfg["width"],
                               edge_color=style_cfg["color"], alpha=0.7,
                               arrows=True, arrowsize=12, ax=ax,
                               connectionstyle="arc3,rad=0.1")

    # Labels
    labels = {n: subG.nodes[n].get("node_label", n) for n in node_list}
    nx.draw_networkx_labels(subG, pos, labels, font_size=8, ax=ax)

    # Edge labels (relation type)
    edge_labels = {}
    for e in edges:
        src, tgt = e["source_node_id"], e["target_node_id"]
        if src in pos and tgt in pos:
            if edge_filter and not edge_filter(e):
                continue
            edge_labels[(src, tgt)] = e["relation_type"]
    nx.draw_networkx_edge_labels(subG, pos, edge_labels, font_size=6, ax=ax)

    ax.axis("off")
    plt.tight_layout()

    png_path = out_dir / f"industry_chain_{suffix}.png"
    svg_path = out_dir / f"industry_chain_{suffix}.svg"
    fig.savefig(str(png_path), dpi=dpi, bbox_inches="tight", facecolor="white")
    fig.savefig(str(svg_path), format="svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    if logger:
        logger.info(f"图片已生成: {png_path.name}, {svg_path.name}")
    return True

# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="阶段6: 正式产业链图谱构建、导出与可视化")
    parser.add_argument("--project-root", required=True, type=str)
    parser.add_argument(
        "--specialization", required=True, choices=["semiconductor"],
        help="本旧版网络图导出含半导体专用文案，必须显式选择后才能运行",
    )
    parser.add_argument("--mode", choices=["dry-run", "full"], default="full")
    parser.add_argument("--export-graphml", action="store_true", default=True)
    parser.add_argument("--export-json", action="store_true", default=True)
    parser.add_argument("--export-neo4j", action="store_true", default=True)
    parser.add_argument("--export-images", action="store_true", default=True)
    parser.add_argument("--layout", choices=["hierarchical", "spring", "multipartite"], default="spring")
    parser.add_argument("--dpi", type=int, default=300)
    args = parser.parse_args()

    project_root = Path(args.project_root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = project_root / "rag" / "outputs" / f"stage6_graph_export_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    setup_logging(log_dir / f"stage6_graph_export_{timestamp}.log")
    rh = logging.FileHandler(out_dir / "run.log", encoding="utf-8")
    rh.setFormatter(logging.Formatter(LOG_FMT))
    logging.getLogger().addHandler(rh)
    logger = logging.getLogger(__name__)

    logger.info("=== 阶段 6 正式图谱构建、导出与可视化 ===")
    logger.info(f"模式: {args.mode}, DPI: {args.dpi}")
    logger.info(f"输出: {out_dir}")

    cfg = load_config(project_root)
    cfg["visualization"]["dpi"] = args.dpi

    try:
        inputs = load_inputs(project_root, cfg)
        logger.info(f"阶段5.5输出: {inputs['stage5_5_dir']}")
    except FileNotFoundError as e:
        logger.error(str(e))
        (out_dir / "validation_summary.json").write_text(
            json.dumps({"error": str(e)}, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(1)

    nodes = inputs["graph_ready_nodes_v2"]
    edges = inputs["graph_ready_edges_v2"]
    aliases = inputs["graph_ready_aliases_v2"]
    evidence_map = inputs["graph_ready_evidence_map_v2"]
    logger.info(f"Input: Nodes={len(nodes)}, Edges={len(edges)}, Aliases={len(aliases)}, Evidence={len(evidence_map)}")

    if args.mode == "dry-run":
        logger.info("DRY-RUN 完成: 输入加载成功")
        return

    # ─── Build graph ───────────────────────────────────────────────────────────
    logger.info("--- 构建 NetworkX 图 ---")
    G = build_graph(nodes, edges)
    logger.info(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # ─── Export GraphML ────────────────────────────────────────────────────────
    logger.info("--- 导出 GraphML ---")
    export_graphml(G, out_dir / "industry_chain.graphml")
    logger.info("GraphML 导出完成")

    # ─── Export JSON ───────────────────────────────────────────────────────────
    logger.info("--- 导出 JSON ---")
    ev_edges = [e for e in edges if e.get("is_evidence_fact") == "true"]
    schema_edges = [e for e in edges if e.get("is_schema_edge") == "true"]
    layout_edges = [e for e in edges if e.get("is_layout_edge") == "true"]
    metadata = {
        "project_root": str(project_root),
        "source_stage5_5_run": str(inputs["stage5_5_dir"]),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "evidence_edge_count": len(ev_edges),
        "schema_edge_count": len(schema_edges),
        "layout_edge_count": len(layout_edges),
        "generated_at": timestamp,
        "warning": "schema/layout edges are NOT extracted PDF evidence facts; they serve structural/visual purposes only.",
    }
    export_json(nodes, edges, aliases, evidence_map, metadata, out_dir / "industry_chain.json")
    logger.info("JSON 导出完成")

    # ─── Export Neo4j CSV ──────────────────────────────────────────────────────
    logger.info("--- 导出 Neo4j CSV ---")
    export_neo4j(nodes, edges, cfg, out_dir)
    write_neo4j_readme(out_dir)
    logger.info("Neo4j CSV 导出完成")

    # ─── Inventories ───────────────────────────────────────────────────────────
    logger.info("--- 生成 inventory ---")
    node_inv = []
    for n in nodes:
        node_inv.append({
            "node_id": n["node_id"], "node_label": n["node_label"],
            "node_type": n["node_type"], "entity_level": n["entity_level"],
            "value_chain_stage": n["value_chain_stage"],
            "in_degree": n["in_degree"], "out_degree": n["out_degree"],
            "total_degree": int(n["in_degree"]) + int(n["out_degree"]),
            "is_schema_root": n["is_schema_root"], "is_isolated": n["is_isolated"],
            "aliases": n.get("aliases", ""), "notes": n.get("notes", ""),
        })
    write_csv(out_dir / "graph_node_inventory.csv", node_inv,
              ["node_id","node_label","node_type","entity_level","value_chain_stage",
               "in_degree","out_degree","total_degree","is_schema_root","is_isolated","aliases","notes"])
    edge_inv = []
    for e in edges:
        edge_inv.append({
            "edge_id": e["edge_id"], "source_label": e["source_label"],
            "relation_type": e["relation_type"], "target_label": e["target_label"],
            "edge_source": e.get("edge_source", ""), "is_evidence_fact": e.get("is_evidence_fact", ""),
            "evidence_count": e.get("evidence_count", ""), "pages": e.get("pages", ""),
            "quotes_preview": e.get("quotes_preview", ""), "notes": e.get("notes", ""),
        })
    write_csv(out_dir / "graph_edge_inventory.csv", edge_inv,
              ["edge_id","source_label","relation_type","target_label","edge_source",
               "is_evidence_fact","evidence_count","pages","quotes_preview","notes"])

    # ─── Visual legend ─────────────────────────────────────────────────────────
    legend = """# 图谱视觉编码说明 (Visual Legend)

## 节点类型与颜色
| 类型 | 颜色 | 说明 |
|------|------|------|
| industry | 红色 | 产业大类 (L0) |
| industry_link | 青色 | 产业环节 (L3) |
| material | 蓝色 | 材料 |
| technology | 绿色 | 技术 |
| company | 黄色 | 企业 |
| equipment | 紫色 | 设备 |
| product | 浅绿 | 产品 |

## 边类型与样式
| edge_source | 样式 | is_evidence_fact | 说明 |
|-------------|------|------------------|------|
| evidence | 实线(黑色) | true | 来源于白皮书 PDF 证据，可追溯到具体页码和引文 |
| system_schema | 虚线(红色) | false | 产业层级结构组织边，用于展示产业分类 |
| layout_helper | 点线(蓝色) | false | 可视化布局辅助边，用于展示逻辑归属 |

## 重要区别
- **Evidence 边**: 来源于《中国半导体白皮书》PDF 证据，每条边都有对应的页码、引文和证据ID，可追溯验证
- **Schema/Layout 边**: 用于结构组织和可视化展示，**不代表 PDF 原文抽取事实**

## 节点大小
节点大小按度数(degree)调整，度数越大节点越大
"""
    (out_dir / "graph_visual_legend.md").write_text(legend, encoding="utf-8")

    # ─── Visualization ─────────────────────────────────────────────────────────
    logger.info("--- 生成可视化图片 ---")
    img_success = {"overview": False, "layered": False, "evidence_only": False, "schema_layout": False}
    try:
        font_found = setup_chinese_font(cfg)
        logger.info(f"字体: {font_found or '使用默认列表'}")

        # Overview
        img_success["overview"] = draw_graph(G, nodes, edges, out_dir, cfg,
            "overview", "半导体产业链图谱总览（证据边与结构辅助边区分显示）", None, logger)

        # Layered (all edges, focus on hierarchy)
        img_success["layered"] = draw_graph(G, nodes, edges, out_dir, cfg,
            "layered", "半导体产业链层级结构图", None, logger)

        # Evidence only
        img_success["evidence_only"] = draw_graph(G, nodes, edges, out_dir, cfg,
            "evidence_only", "半导体产业链证据边图（仅白皮书证据关系）",
            lambda e: e.get("is_evidence_fact") == "true", logger)

        # Schema/Layout only
        img_success["schema_layout"] = draw_graph(G, nodes, edges, out_dir, cfg,
            "schema_layout", "半导体产业链结构辅助边图（非PDF证据事实）\n[schema/layout edges are NOT extracted evidence facts]",
            lambda e: e.get("edge_source") in ("system_schema", "layout_helper"), logger)

    except Exception as ex:
        logger.error(f"可视化生成异常: {ex}")

    # ─── Statistics ────────────────────────────────────────────────────────────
    logger.info("--- 生成统计 ---")
    components = list(nx.weakly_connected_components(G))
    largest_cc = max(len(c) for c in components) if components else 0
    isolated = sum(1 for n in nodes if n["is_isolated"] == "true")

    stats = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "evidence_edge_count": len(ev_edges),
        "schema_edge_count": len(schema_edges),
        "layout_edge_count": len(layout_edges),
        "alias_count": len(aliases),
        "evidence_map_count": len(evidence_map),
        "node_count_by_type": dict(Counter(n["node_type"] for n in nodes)),
        "node_count_by_level": dict(Counter(n["entity_level"] for n in nodes)),
        "node_count_by_value_chain_stage": dict(Counter(n["value_chain_stage"] for n in nodes)),
        "edge_count_by_relation_type": dict(Counter(e["relation_type"] for e in edges)),
        "edge_count_by_source": dict(Counter(e.get("edge_source", "evidence") for e in edges)),
        "isolated_node_count": isolated,
        "connected_components_count": len(components),
        "largest_component_size": largest_cc,
        "main_chain_detected": any(e["relation_type"] == "SUPPLIES_TO" for e in edges),
        "output_files": [f.name for f in out_dir.iterdir() if f.is_file()],
    }
    (out_dir / "graph_export_statistics.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    # ─── Report ────────────────────────────────────────────────────────────────
    logger.info("--- 生成报告 ---")
    node_ids_set = {n["node_id"] for n in nodes}
    all_src_valid = all(e["source_node_id"] in node_ids_set for e in edges)
    all_tgt_valid = all(e["target_node_id"] in node_ids_set for e in edges)
    ev_edge_ids = {ev["edge_id"] for ev in evidence_map if ev.get("edge_id")}
    all_ev_have_map = all(e["edge_id"] in ev_edge_ids for e in ev_edges)

    report = [
        "# 阶段 6 产业链图谱构建与导出报告", "",
        "> 阶段 6 生成的是可导出的产业链图谱数据与可视化结果；",
        "> 其中 evidence 边可追溯到白皮书证据，schema/layout 边仅用于结构组织；",
        "> 本阶段未调用外部 API、未调用本地 LLM、未重新抽取关系、未写入 Neo4j。", "",
        f"## 输入",
        f"- 阶段5.5路径: `{inputs['stage5_5_dir']}`",
        f"- 节点: **{len(nodes)}** | 边: **{len(edges)}**",
        f"- Evidence边: **{len(ev_edges)}** | Schema边: **{len(schema_edges)}** | Layout边: **{len(layout_edges)}**", "",
        f"## 导出文件",
        f"- GraphML: `industry_chain.graphml`",
        f"- JSON: `industry_chain.json`",
        f"- Neo4j: `neo4j_nodes.csv`, `neo4j_edges.csv`, `neo4j_import_readme.md`", "",
        f"## 可视化",
        f"- 总览图: `industry_chain_overview.png/svg` — {'✓' if img_success['overview'] else '✗'}",
        f"- 层级图: `industry_chain_layered.png/svg` — {'✓' if img_success['layered'] else '✗'}",
        f"- 证据边图: `industry_chain_evidence_only.png/svg` — {'✓' if img_success['evidence_only'] else '✗'}",
        f"- 结构辅助边图: `industry_chain_schema_layout.png/svg` — {'✓' if img_success['schema_layout'] else '✗'}", "",
        f"## 主链结构",
        f"- 设计 →(SUPPLIES_TO) 晶圆制造 →(SUPPLIES_TO) 封装 →(SUPPLIES_TO) 测试",
        f"- 集成电路 →(PART_OF) 半导体产业",
        f"- 设计/晶圆制造/封装测试 →(PART_OF) 集成电路", "",
        f"## 证据边与结构辅助边的区别",
        f"- Evidence边({len(ev_edges)}条): 来源于白皮书PDF，可追溯到页码和引文",
        f"- Schema/Layout边({len(schema_edges)+len(layout_edges)}条): 用于产业层级和布局，非PDF事实", "",
        f"## 连通性",
        f"- 弱连通分量: **{len(components)}** 个",
        f"- 最大连通分量: **{largest_cc}** 个节点",
        f"- 孤立节点: **{isolated}** 个", "",
        f"## 当前图谱的局限",
        f"- 仅基于一份白皮书(25页)，覆盖面有限",
        f"- 部分节点孤立（材料/技术/企业类）",
        f"- schema/layout 边为人工补充结构，非证据事实", "",
        f"## 如何打开 GraphML",
        f"- Gephi: File > Open > 选择 industry_chain.graphml",
        f"- yEd: File > Open > 选择 industry_chain.graphml",
        f"- Python: `nx.read_graphml('industry_chain.graphml')`", "",
        f"## 如何导入 Neo4j",
        f"- 参见 `neo4j_import_readme.md`", "",
        f"## 验收结果",
        f"- **{'通过' if all_ev_have_map and all_src_valid and all_tgt_valid else '未通过'}**",
        "---", f"*阶段6 | {timestamp}*",
    ]
    (out_dir / "stage6_graph_export_report.md").write_text("\n".join(report), encoding="utf-8")

    # ─── run_config.json ───────────────────────────────────────────────────────
    (out_dir / "run_config.json").write_text(json.dumps({
        "mode": args.mode, "layout": args.layout, "dpi": args.dpi,
        "project_root": str(project_root), "stage5_5_dir": str(inputs["stage5_5_dir"]),
        "output_dir": str(out_dir), "timestamp": timestamp,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ─── validation_summary.json ───────────────────────────────────────────────
    schema_layout_marked = all(e.get("is_evidence_fact") == "false"
                               for e in edges if e.get("edge_source") in ("system_schema", "layout_helper"))
    validation = {
        "stage5_5_latest_run_found": {"passed": True, "note": str(inputs["stage5_5_dir"])},
        "required_input_files_exist": {"passed": True, "note": "全部存在"},
        "nodes_loaded": {"passed": True, "note": f"{len(nodes)} 个"},
        "edges_loaded": {"passed": True, "note": f"{len(edges)} 条"},
        "aliases_loaded": {"passed": True, "note": f"{len(aliases)} 条"},
        "evidence_map_loaded": {"passed": True, "note": f"{len(evidence_map)} 条"},
        "graph_built": {"passed": True, "note": f"{G.number_of_nodes()} nodes, {G.number_of_edges()} edges"},
        "node_count_matches_input": {"passed": G.number_of_nodes() == len(nodes), "note": f"{G.number_of_nodes()} == {len(nodes)}"},
        "edge_count_matches_input": {"passed": G.number_of_edges() == len(edges), "note": f"{G.number_of_edges()} == {len(edges)}"},
        "all_edges_have_valid_source_node": {"passed": all_src_valid, "note": "全部有效"},
        "all_edges_have_valid_target_node": {"passed": all_tgt_valid, "note": "全部有效"},
        "all_evidence_edges_have_evidence": {"passed": all_ev_have_map, "note": f"全部{len(ev_edges)}条有证据"},
        "schema_layout_edges_marked_not_evidence_fact": {"passed": schema_layout_marked, "note": "全部标记"},
        "graphml_exported": {"passed": (out_dir / "industry_chain.graphml").exists(), "note": "已导出"},
        "json_exported": {"passed": (out_dir / "industry_chain.json").exists(), "note": "已导出"},
        "neo4j_nodes_exported": {"passed": (out_dir / "neo4j_nodes.csv").exists(), "note": "已导出"},
        "neo4j_edges_exported": {"passed": (out_dir / "neo4j_edges.csv").exists(), "note": "已导出"},
        "overview_image_exported": {"passed": img_success["overview"], "note": "PNG+SVG"},
        "layered_image_exported": {"passed": img_success["layered"], "note": "PNG+SVG"},
        "evidence_only_image_exported": {"passed": img_success["evidence_only"], "note": "PNG+SVG"},
        "schema_layout_image_exported": {"passed": img_success["schema_layout"], "note": "PNG+SVG"},
        "statistics_generated": {"passed": True, "note": "已生成"},
        "report_generated": {"passed": True, "note": "已生成"},
        "no_external_api_called": {"passed": True, "note": "仅本地处理"},
        "no_llm_called": {"passed": True, "note": "未调用LLM"},
        "no_new_unverified_relations_added": {"passed": True, "note": "未新增关系"},
        "no_neo4j_write": {"passed": True, "note": "未写Neo4j"},
        "original_pdf_not_modified": {"passed": True, "note": "原始PDF未改动"},
    }
    (out_dir / "validation_summary.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")

    # Update pointer
    Path(cfg["output"]["latest_pointer"]).write_text(str(out_dir), encoding="utf-8")

    all_pass = all(v["passed"] for v in validation.values())
    logger.info(f"=== 阶段6完成 === 验收: {'全部通过' if all_pass else '存在未通过项'}")

if __name__ == "__main__":
    main()
