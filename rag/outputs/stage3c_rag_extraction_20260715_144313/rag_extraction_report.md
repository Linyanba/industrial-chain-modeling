# 阶段 3C 候选实体/关系抽取报告

- 生成时间: 20260715_144313
- Qdrant 地址 / collection: http://localhost:6333 / kb_74af4f035d18
- 文档过滤 doc_id: 未指定
- Embedding 模型: Qwen/Qwen3-Embedding-0.6B (huggingface/cpu)
- 生成模型: qwen3:8b
- 是否使用 Ollama /api/generate: 是（仅在 generate 失败时才回退 /api/chat）
- 任务数量: 10

## 各任务检索与抽取概览

| 任务 | 主要命中页 | 实体候选 | verified实体 | 关系候选 | verified关系 |
|---|---|---|---|---|---|
| t01 | [10, 22, 23] | 16 | 9 | 14 | 0 |
| t02 | [5, 6, 10, 22, 23] | 3 | 1 | 0 | 0 |
| t03 | [10, 22, 23] | 12 | 12 | 12 | 0 |
| t04 | [6, 10] | 16 | 14 | 14 | 0 |
| t05 | [5, 6, 10, 22, 23] | 9 | 9 | 8 | 0 |
| t06 | [5, 6, 10, 22, 23] | 0 | 0 | 0 | 0 |
| t07 | [10, 22, 23] | 13 | 13 | 8 | 0 |
| t08 | [10, 22] | 12 | 12 | 10 | 10 |
| t09 | [6, 10, 23] | 15 | 12 | 15 | 0 |
| t10 | [5, 6, 10, 22, 23] | 0 | 0 | 0 | 0 |

## 数量汇总

- 候选实体总数 / verified: 96 / 82
- 候选关系总数 / verified: 81 / 10
- 被拒绝候选: 66
- 复核队列项: 85

### 按实体类型统计 (全部候选)

- material: 23
- product: 19
- industry_link: 18
- process: 12
- technology: 9
- equipment: 5
- application: 3
- unknown: 3
- industry: 2
- company: 2

### 按关系类型统计 (全部候选)

- INPUT_TO: 38
- USED_IN: 21
- PART_OF: 10
- OUTPUT_OF: 9
- SUPPLY: 2
- DEVELOPED_FOR: 1

### 拒绝原因统计

- relation_type_invalid: 18
- subject_or_object_not_found: 18
- entity_type_invalid: 12
- quote_not_found: 11
- evidence_id_not_in_context: 7

### 复核队列原因统计

- relation_type_invalid: 18
- subject_or_object_not_found: 18
- relation_endpoint_type_mismatch: 12
- entity_type_invalid: 12
- quote_not_found: 11
- evidence_id_not_in_context: 7
- non_atomic_relation_endpoint: 5
- relation_cue_missing: 2

## 质量评价

- 是否出现无证据关系: 否
- quote 匹配失败数: 11
- verified 关系是否均为 explicit_fact: 是
- 主要命中页码(全体): [5, 6, 10, 16, 22, 23, 24]

## 阶段 4 建议

- 实体标准化：对 normalized_name_candidate 做同义归并与别名映射。
- 关系去重：按 (subject_norm, relation_type, object_norm) 去重，合并 evidence 列表。
- 冲突检测：检查方向矛盾与重复关系，标记矛盾对。
- 人工审核：优先处理 review_queue 中 high 项与 forecast/policy 类，确认后再入图。

## 验证项

- PASS qdrant_connected: HTTP 200
- PASS collection_exists: kb_74af4f035d18
- PASS collection_has_points: points_count>0
- PASS doc_id_filter_set: 中国半导体白皮书_1b73a8b1f91b
- PASS allowed_doc_ids_set: 中国半导体白皮书_1b73a8b1f91b
- PASS ollama_connected: http://localhost:11434
- PASS llm_model_available: qwen3:8b
- PASS tasks_loaded: 10 个任务
- PASS embedding_model_loaded: Qwen/Qwen3-Embedding-0.6B (huggingface/cpu)
- PASS retrieval_completed: 10/10
- PASS retrieval_limited_to_current_doc: retrieved_doc_ids=['中国半导体白皮书_1b73a8b1f91b'], expected=中国半导体白皮书_1b73a8b1f91b
- PASS model_calls_completed: 10 次调用
- PASS entity_candidates_generated: 96 个
- PASS relation_candidates_generated: 81 个
- PASS verified_entities_generated: 82 个
- PASS verified_relations_generated: 10 个
- PASS json_outputs_valid_or_reviewed: 失败 0
- PASS all_verified_entities_have_evidence: 82 个 verified 实体
- PASS all_verified_relations_have_evidence: 10 个 verified 关系
- PASS verified_quotes_match_source_text: 92 个 verified 候选已通过原文校验
- PASS verified_relations_are_explicit_fact: verified 关系均 explicit_fact
- PASS no_table_or_diagram_visual_relation_verified: 无图表 verified 关系
- PASS no_forecast_or_policy_goal_verified_as_fact: 无预测/政策目标被当作事实
- PASS no_external_api_called: 仅本地 Qdrant + Ollama
- PASS no_graph_generated: 未生成图谱/未写 Neo4j
- PASS original_pdf_not_modified: 本阶段只读 Qdrant 与配置，不写入 PDF

---
> 本阶段只完成基于 Qdrant 检索证据的 RAG 辅助候选实体与候选关系抽取；verified 仅表示通过原文与规则校验的候选，不代表人工最终确认；未生成正式产业链图谱；未写入 Neo4j；未做实体最终归并；未调用任何外部云端 API 或联网搜索。