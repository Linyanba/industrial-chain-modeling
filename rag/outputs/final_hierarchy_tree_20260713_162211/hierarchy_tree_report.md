# 产业链建模项目 — 最终输出重构报告

## 使用模板: document_driven

## 为什么原阶段6 network graph 不符合最终目标
原阶段6输出为 GraphML/Neo4j/力导向网络图，适合知识图谱研究，
但用户需要的是**类似思维导图的多级产业链结构图**（左到右展开、蓝色圆角矩形节点）。
network graph 无法清晰展示产业链层级结构，因此从最终交付中移除。

## 归档的文件
- 共 0 个 network graph 相关文件已归档
- 归档目录: `D:\产业链建模\archive\deprecated_network_graph_outputs/20260713_162211/`
- 这些文件未被删除，可在需要知识图谱交付时重新启用

## 最终树结构
```
半导体产业链
├── 晶圆制造
│   ├── 光刻
│   ├── 刻蚀
│   ├── 沉积
│   └── 离子注入
└── 计算芯片
    ├── ASIC
    └── GPU
```

## 展示边与证据边的区别
- 本结构图中所有父子边均为 DISPLAY_PARENT_OF，is_evidence_fact=false
- 这些边用于展示产业链层级结构，不代表 PDF 原文事实
- 证据追溯仍来自前序阶段的 evidence_map

---
> 最终交付以多级产业链结构图为主，不再以 network graph/Neo4j/GraphML 可视化为主。