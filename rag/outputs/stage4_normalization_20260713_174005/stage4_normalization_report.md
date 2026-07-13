# 阶段 4 标准化报告

- 生成时间: 20260713_174005
- 输入目录: D:\产业链建模\rag\outputs\stage3c_rag_extraction_20260713_172751
- 输入 verified 实体: 81
- 输入 verified 关系: 9
- 输入 review 队列: 84
- 输入 rejected: 63

## 实体标准化

- 方法: 基础文本归一化(全角半角/大小写/标点/空白) + 按 normalized_key 分组
- canonical 实体数: 45
- 别名数: 1

  - process: 8
  - material: 8
  - industry_link: 6
  - product: 6
  - technology: 5
  - equipment: 4
  - unknown: 3
  - application: 3
  - company: 2

## 关系去重

- 方法: 按 (subject_canonical_id, relation_type, object_canonical_id) 去重合并证据
- normalized 关系数: 9
- 证据映射条目: 9

  - PART_OF: 9

## 冲突检测

- 冲突数: 0

## 复核队列

- manual_review_sheet 条目: 88
  - P0: 0
  - P1: 20
  - P2: 1
  - P3: 67

## rejected 重检

- stage4_rejected_recheck 条目: 28

## 重要声明

- 阶段 4 只完成候选结果清洗、标准化、去重和审核准备
- canonical_entities 与 normalized_relations 仍然是待审核草案
- 尚未完成人工确认
- 尚未生成正式产业链图谱
- 尚未写入 Neo4j
- 不得把本阶段输出视为最终事实库

## 阶段 5 人工审核建议

- 优先处理 P0 主链骨架关系（PART_OF 产业链层级）
- 其次处理 P1 材料/设备/技术/工艺与环节关系
- 确认同名不同类型冲突
- 核对 quote 匹配失败的 rejected 重检项
- 最终确认后才能生成正式图谱和写入 Neo4j

## 验证项

- PASS stage3c_latest_run_found: D:\产业链建模\rag\outputs\stage3c_rag_extraction_20260713_172751
- PASS required_input_files_exist: 全部存在
- PASS verified_entities_loaded: 81 个
- PASS verified_relations_loaded: 9 个
- PASS review_queue_loaded: 84 条
- PASS rejected_candidates_loaded: 63 条
- PASS canonical_entities_generated: 44 个
- PASS entity_aliases_generated: 1 个
- PASS normalized_relations_generated: 9 条
- PASS relation_evidence_map_generated: 9 条
- PASS conflict_report_generated: 0 个冲突
- PASS manual_review_sheet_generated: 88 条
- PASS stage4_review_queue_generated: 84 条
- PASS industry_chain_draft_generated: 已生成
- PASS no_external_api_called: 仅本地规则处理
- PASS no_new_relations_extracted: 未新增关系
- PASS no_graph_generated: 未生成图谱/未写 Neo4j
- PASS original_pdf_not_modified: 本阶段不写入 PDF

---
> 阶段 4 仅完成候选清洗与审核准备，所有输出为待审核草案。