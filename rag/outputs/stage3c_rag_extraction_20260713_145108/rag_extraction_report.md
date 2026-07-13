# 阶段 3C 候选实体/关系抽取报告

- 生成时间: 20260713_145108
- Qdrant 地址 / collection: http://localhost:6333 / kb_f96baba2fc0d
- 文档过滤 doc_id: 未指定
- Embedding 模型: Qwen/Qwen3-Embedding-0.6B (huggingface/cpu)
- 生成模型: qwen3:8b
- 是否使用 Ollama /api/generate: 是（仅在 generate 失败时才回退 /api/chat）
- 任务数量: 10

## 各任务检索与抽取概览

| 任务 | 主要命中页 | 实体候选 | verified实体 | 关系候选 | verified关系 |
|---|---|---|---|---|---|
| t01 | [11, 33] | 8 | 7 | 4 | 0 |
| t02 | [3, 4, 11, 33] | 8 | 6 | 5 | 0 |
| t03 | [18, 30, 33] | 8 | 8 | 8 | 0 |
| t04 | [11, 17, 29, 33] | 5 | 5 | 3 | 0 |
| t05 | [3, 4, 17, 32] | 5 | 5 | 5 | 0 |
| t06 | [3, 4, 34] | 0 | 0 | 0 | 0 |
| t07 | [2, 16, 30, 33] | 12 | 9 | 8 | 0 |
| t08 | [4, 11, 17, 18] | 10 | 10 | 7 | 7 |
| t09 | [11, 16, 29, 30, 31] | 9 | 8 | 7 | 1 |
| t10 | [3, 4, 11, 33] | 7 | 7 | 6 | 0 |

## 数量汇总

- 候选实体总数 / verified: 72 / 65
- 候选关系总数 / verified: 53 / 8
- 被拒绝候选: 44
- 复核队列项: 52

### 按实体类型统计 (全部候选)

- material: 24
- product: 15
- industry_link: 9
- industry: 8
- technology: 6
- company: 3
- process: 2
- application: 2
- policy: 2
- region: 1

### 按关系类型统计 (全部候选)

- INPUT_TO: 20
- SUPPLIES_TO: 9
- SERVES: 8
- OUTPUT_OF: 7
- PART_OF: 7
- REGULATES: 2

### 拒绝原因统计

- subject_or_object_not_found: 37
- quote_not_found: 7

### 复核队列原因统计

- subject_or_object_not_found: 37
- quote_not_found: 7
- relation_cue_missing: 4
- relation_endpoint_type_mismatch: 3
- forecast_keyword_in_quote:鼓励: 1

## 质量评价

- 是否出现无证据关系: 否
- quote 匹配失败数: 7
- verified 关系是否均为 explicit_fact: 是
- 主要命中页码(全体): [2, 3, 4, 5, 11, 16, 17, 18, 29, 30, 31, 32, 33, 34, 35]

## 阶段 4 建议

- 实体标准化：对 normalized_name_candidate 做同义归并与别名映射。
- 关系去重：按 (subject_norm, relation_type, object_norm) 去重，合并 evidence 列表。
- 冲突检测：检查方向矛盾与重复关系，标记矛盾对。
- 人工审核：优先处理 review_queue 中 high 项与 forecast/policy 类，确认后再入图。

## 验证项

- PASS qdrant_connected: HTTP 200
- PASS collection_exists: kb_f96baba2fc0d
- PASS collection_has_points: points_count>0
- PASS doc_id_filter_set: 新型显示白皮书_7f4f945d7c21
- PASS allowed_doc_ids_set: 新型显示白皮书_7f4f945d7c21
- PASS ollama_connected: http://localhost:11434
- PASS llm_model_available: qwen3:8b
- PASS tasks_loaded: 10 个任务
- PASS embedding_model_loaded: Qwen/Qwen3-Embedding-0.6B (huggingface/cpu)
- PASS retrieval_completed: 10/10
- PASS retrieval_limited_to_current_doc: retrieved_doc_ids=['新型显示白皮书_7f4f945d7c21'], expected=新型显示白皮书_7f4f945d7c21
- PASS model_calls_completed: 10 次调用
- PASS entity_candidates_generated: 72 个
- PASS relation_candidates_generated: 53 个
- PASS verified_entities_generated: 65 个
- PASS verified_relations_generated: 8 个
- PASS json_outputs_valid_or_reviewed: 失败 0
- PASS all_verified_entities_have_evidence: 65 个 verified 实体
- PASS all_verified_relations_have_evidence: 8 个 verified 关系
- PASS verified_quotes_match_source_text: 73 个 verified 候选已通过原文校验
- PASS verified_relations_are_explicit_fact: verified 关系均 explicit_fact
- PASS no_table_or_diagram_visual_relation_verified: 无图表 verified 关系
- PASS no_forecast_or_policy_goal_verified_as_fact: 无预测/政策目标被当作事实
- PASS no_external_api_called: 仅本地 Qdrant + Ollama
- PASS no_graph_generated: 未生成图谱/未写 Neo4j
- PASS original_pdf_not_modified: 本阶段只读 Qdrant 与配置，不写入 PDF

---
> 本阶段只完成基于 Qdrant 检索证据的 RAG 辅助候选实体与候选关系抽取；verified 仅表示通过原文与规则校验的候选，不代表人工最终确认；未生成正式产业链图谱；未写入 Neo4j；未做实体最终归并；未调用任何外部云端 API 或联网搜索。