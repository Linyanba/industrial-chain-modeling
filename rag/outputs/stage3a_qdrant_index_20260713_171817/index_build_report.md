# 阶段 3A：Qdrant 向量索引构建报告

- 生成时间：2026-07-13 17:27:47
- 解析目录：`D:\产业链建模\parsed_documents\中国半导体白皮书_1b73a8b1f91b`
- 文档 ID：`中国半导体白皮书_1b73a8b1f91b`

## 1. 基本信息
1. Qdrant 地址：`http://localhost:6333`
2. collection 名称：`kb_74af4f035d18`
3. embedding 模型：`Qwen/Qwen3-Embedding-0.6B` (加载来源：huggingface)
4. embedding 维度：1024
5. 是否使用 GPU：否 (CPU)

## 2. 数据统计
6. 输入 evidence_chunks 总数：720
7. 被选入索引的 chunk 数：94
8. 被排除的 chunk 数：626

排除原因统计：

| 原因 | 数量 |
|---|---|
| review_required | 379 |
| garbled_text | 231 |
| table | 9 |
| not_in_allowlist | 5 |
| cover_page | 1 |
| toc_page | 1 |

## 3. 写入结果
9. 成功写入 Qdrant 的 point 数：94
10. 失败 chunk 数：0
- collection 当前 point_count：94

## 4. Payload 字段说明
11. 每个 point 的 payload 至少包含以下字段：

| 字段 | 说明 |
|---|---|
| evidence_id | 原始证据块 ID |
| doc_id | 文档 ID |
| page_start | 起始页码 |
| page_end | 结束页码 |
| section_path | 章节路径（列表） |
| content_type | 内容类型 paragraph/caption/heading/ocr_text |
| title_context | 标题上下文 |
| stage3_priority | 阶段 3 优先级 high/medium/low |
| review_required | 是否需要人工复核 |
| parse_quality | 解析质量分 |
| source_parser | 来源解析器 |
| text | text_normalized 或 text_raw |
| text_hash | text 的 sha256 |
| source_file | 来源文件 evidence_chunks.jsonl |

## 5. Payload 索引创建情况
12. 索引字段状态：

| 字段 | 状态 |
|---|---|
| doc_id | created |
| root_model | created |
| page_start | created |
| content_type | created |
| stage3_priority | created |
| review_required | created |

## 6. 检索冒烟测试摘要
13. 每条查询 top 8，命中概览：

| 查询 | top1 score | top1 页码 | top1 类型 | top1 优先级 |
|---|---|---|---|---|
| 当前白皮书中的产业链包括哪些主要环节？ | 0.5792 | 5 | paragraph | low |
| 上游 中游 下游 核心模块 | 0.4775 | 23 | paragraph | high |
| 关键材料 部件 设备 工艺 技术 服务 | 0.5363 | 10 | paragraph | high |
| 产品 系统 解决方案 应用场景 | 0.3127 | 24 | paragraph | low |
| 包括 包含 分为 由 构成 | 0.3085 | 16 | heading | low |
| 输入 输出 供给 服务 使用 制造 | 0.3557 | 10 | paragraph | high |
| 政策 趋势 规划 目标 建议 | 0.3561 | 23 | heading | high |

## 7. 当前问题与风险
14. 说明：
- 当前 PyTorch 为 CPU 版本，CUDA 不可用；embedding 使用 CPU，规模扩大时速度受限（本阶段数据量小，影响可忽略）。

## 8. 下一步建议
15. 建议：
- 可考虑对表格类内容做结构化提取后再入库（本阶段已排除 table）。
- 如需 GPU 加速，安装 CUDA 版 PyTorch。
- 检索质量确认后，进入阶段 3B：接入 Qwen3:8B 做基于证据的问答。

## 边界声明
```text
本阶段只完成 Qdrant 向量索引构建；
未调用生成式大模型；
未进行问答；
未抽取产业链实体或关系；
后续步骤才会接入 Qwen3:8B 进行基于证据的问答或候选关系抽取。
```