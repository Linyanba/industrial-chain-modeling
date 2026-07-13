# 模板应用报告

- 使用模板: document_driven
- 模板名称: 文档驱动产业链模板
- root_label: 新型显示产业链
- 一级节点生成方式: 规则评分 + 可选本地Ollama建议；最终结果经程序校验
- 本地LLM建议: 未使用或不可用，已fallback到规则法
- LLM原始建议hash: N/A
- 是否发生fallback_display_schema: 否
- 未分类实体: 7
- 程序校验: 通过

## 一级节点及证据支持
- 先进电子材料: score=2, entity_count=2, relation_count=1, evidence_count=1
- 新型显示器件: score=3, entity_count=3, relation_count=2, evidence_count=1
- 新型显示技术: score=5, entity_count=5, relation_count=4, evidence_count=1
- 液晶面板: score=2, entity_count=2, relation_count=1, evidence_count=1
- 新型显示材料: score=10, entity_count=10, relation_count=0, evidence_count=6
- 新型显示核心环节: score=5, entity_count=5, relation_count=0, evidence_count=2
- 新型显示产品与应用: score=4, entity_count=4, relation_count=0, evidence_count=3
- 新型显示设备: score=2, entity_count=2, relation_count=0, evidence_count=1

## 节点来源
- PDF抽取实体节点: 33
- display_schema_node: 4

## 为什么没有默认使用“上游/中游/下游”
document_driven 模板禁止将“上游/中游/下游”作为默认一级节点；本次一级节点来自已审核实体类型、关系连接、章节标题与证据命中后的综合评分。

## 结构不理想时如何调整
优先检查 approved_entities.csv、approved_relations.csv 和章节标题质量；必要时提高实体审核质量，或在 document_driven.yaml 中调整 max_first_level_nodes 与评分权重。