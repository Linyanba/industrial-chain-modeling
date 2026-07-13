# 阶段 4.5 人工审核压缩报告

> 阶段 4.5 只完成保守自动审核与人工审核最小化。
> 输出是极少人工审核模式下的保守草案，**不是最终产业链图谱**。
> 被 auto_defer 或 archive 的内容不是永久删除，只是暂时不进入保守草案。
> 如果后续追求更高召回率，可以重新打开归档项。

## 输入

- 阶段 4 输出路径：`D:\产业链建模\rag\outputs\stage4_normalization_20260713_181032`
- 原始人工复核项数量：**71**
- P0=3, P1=7, P2=0, P3=61

## 自动处理统计

| 决策类型 | 数量 |
|----------|------|
| auto_keep | 0 |
| auto_merge_suggestion | 0 |
| auto_fix_suggestion | 0 |
| auto_defer | 0 |
| auto_archive_rejected | 61 |
| auto_reject_as_fact | 0 |
| manual_required | 10 |
| **合计** | **71** |

## 人工审核压缩结果

- minimal_human_review_sheet 条目数：**7**
- 人工审核压缩比例：**90.1%**（71→7）

## 保守输出

- 保守实体数量：**40**
- 保守关系数量：**8**
- 归档低优先级数量：**61**
- 自动别名建议数量：**0**
- 自动关系修正建议数量：**0**

## 仍需人工确认的核心问题

- [P0] 关系候选待复核: 产业链->INPUT_TO->产业链上下游
- [P0] 关系候选待复核: 新型显示产业->SUPPLIES_TO->产业链
- [P1] 关系候选待复核: 面板制造企业->USES_TECHNOLOGY->配套材料
- [P1] 关系候选待复核: 显示材料产业->SUPPLIES_TO->产业集群
- [P1] 关系候选待复核: 关键原材料->INPUT_TO->显示面板
- [P0] 关系候选待复核: 产业链->INPUT_TO->上下游
- [P1] 关系候选待复核: 玻璃基板->OUTPUT_OF->高世代玻璃基板

## 为什么是高精度低召回

本模式严格遵循以下原则：
- P3 默认归档/暂缓
- P2 默认暂缓
- forecast/trend/investment/policy_goal 默认不作为现实产业关系
- 只保留有明确 quote 证据支撑的关系
- 过泛/过长/层级不清实体不进入保守草案
- 只把必须人工判断的核心结构问题（≤7条）交给用户

## 哪些内容被牺牲

- 所有 P3 的 quote_not_found / subject_or_object_not_found 项
- P1/P2 的 forecast/trend/investment 表述
- 过泛、过长、层级不清或缺少证据支撑的实体
- 端点为过泛实体或缺少 evidence_id 的关系

## 如何提高召回率

1. 从 `archived_low_priority_items.csv` 筛选 `can_revisit_later=true` 的项，
   逐批恢复到审核流程。
2. 从阶段 4 的 `stage4_rejected_recheck.csv` 中重新评估被拒候选。
3. 可传入 `--mode permissive` 放宽阈值（保留更多 P2/P1 项）。
4. 可传入 `--use-local-llm-for-review` 让本地 LLM 辅助判断。

---
*运行模式：conservative | max_human_items=7*