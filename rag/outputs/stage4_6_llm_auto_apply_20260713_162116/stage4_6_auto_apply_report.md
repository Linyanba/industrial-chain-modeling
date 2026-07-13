# 阶段 4.6 本地LLM辅助自动审核报告

> 本阶段输出是自动审核后 approved 草案，**不等于最终事实库**。
> 未覆盖的审核项保留在 remaining_human_review_sheet.csv。

## 输入
- 阶段 4.5 路径: `D:\产业链建模\rag\outputs\stage4_5_review_minimization_20260713_162112`
- minimal_human_review_sheet 条目数: **7**

## 模型调用
- 模型: qwen3:8b
- Ollama 连接: 成功
- 模型可用: 是
- JSON 解析: 成功
- 生成决策数: **7**

## 校验与应用
- 校验通过 actions: **6**
- 校验失败 actions: **1**
- 自动应用补丁: **0**
- 未应用补丁: **6**
- remaining_human_review: **7** 条

## Approved 输出
- Approved 实体: **37**
- Approved 关系: **6**
- Approved 别名: **0**
- 证据映射: **6** 条

## 人工审核压缩结果
- 原始人工审核: 7 条
- 剩余人工审核: **7** 条
- 人工审核是否已压缩到0: **否**

## 声明
- 本阶段输出是自动审核后的 approved 草案，不是最终产业链图谱
- 所有 approved 关系均可回溯到原始证据
- 未调用外部云端 API
- 未生成正式图谱，未写入 Neo4j

## 后续建议
- 如 remaining_human_review_sheet 有剩余项，需人工决定
- approved 文件可作为阶段 5 图谱构建的输入
- 归档项如需回捞，参考阶段 4.5 archived_low_priority_items.csv

---
*运行模式: auto-apply | 模型: qwen3:8b | 时间: 20260713_162116*