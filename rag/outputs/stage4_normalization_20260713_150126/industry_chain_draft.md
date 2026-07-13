# 产业链草案（待人工审核）

> **重要声明**：本文档为阶段 4 自动生成的产业链结构草案，所有实体和关系均为待审核候选，**不得视为最终事实**。尚未完成人工确认、尚未生成正式图谱、尚未写入 Neo4j。

## L0_industry (3 个实体)

- **产业链协同** [industry] (value_chain=cross_stage, degree=0, confidence=medium)
- **新型显示产业** [industry] (value_chain=cross_stage, degree=0, confidence=high)
- **新型显示产业链** [industry] (value_chain=cross_stage, degree=0, confidence=medium)

## L3_industry_link (5 个实体)

- **上下游** [industry_link] (value_chain=unknown, degree=0, confidence=medium)
- **产业生态** [industry_link] (value_chain=unknown, degree=0, confidence=medium)
- **产业链** [industry_link] (value_chain=unknown, degree=0, confidence=high)
- **供应链** [industry_link] (value_chain=unknown, degree=0, confidence=medium)
- **创新链** [industry_link] (value_chain=unknown, degree=0, confidence=medium)

## L4_object (33 个实体)

- **4K/8K 电视** [product] (value_chain=unknown, degree=0, confidence=medium)
- **AR/VR** [application] (value_chain=downstream, degree=0, confidence=medium)
- **OLED 材料** [material] (value_chain=upstream, degree=0, confidence=medium)
- **主动矩阵有机发光二极管显示(AMOLED)** [technology] (value_chain=supporting, degree=1, confidence=medium)
- **产品** [product] (value_chain=unknown, degree=0, confidence=medium)
- **偏光片** [product] (value_chain=unknown, degree=0, confidence=medium)
- **先进电子材料** [material] (value_chain=upstream, degree=1, confidence=medium)
- **全面屏手机** [product] (value_chain=unknown, degree=0, confidence=medium)
- **关键原材料** [material] (value_chain=upstream, degree=0, confidence=medium)
- **可穿戴设备** [application] (value_chain=downstream, degree=0, confidence=medium)
- **工艺** [process] (value_chain=midstream, degree=0, confidence=medium)
- **微型发光二极管显示(Micro-LED)** [technology] (value_chain=supporting, degree=1, confidence=medium)
- **掩模版** [material] (value_chain=upstream, degree=0, confidence=high)
- **新型显示器件** [product] (value_chain=unknown, degree=2, confidence=medium)
- **新型显示技术** [technology] (value_chain=supporting, degree=4, confidence=medium)
- **新型显示材料** [material] (value_chain=upstream, degree=1, confidence=high)
- **显示材料** [material] (value_chain=upstream, degree=0, confidence=high)
- **显示芯片** [material] (value_chain=upstream, degree=0, confidence=medium)
- **显示面板** [product] (value_chain=unknown, degree=0, confidence=high)
- **有机发光二极管显示面板** [product] (value_chain=unknown, degree=1, confidence=medium)
- **有机发光材料** [product] (value_chain=unknown, degree=0, confidence=medium)
- **材料** [material] (value_chain=upstream, degree=0, confidence=medium)
- **核心技术** [technology] (value_chain=supporting, degree=0, confidence=medium)
- **液晶材料** [material] (value_chain=upstream, degree=0, confidence=high)
- **液晶面板** [product] (value_chain=unknown, degree=1, confidence=medium)
- **湿电子化学品** [material] (value_chain=upstream, degree=0, confidence=medium)
- **激光显示** [technology] (value_chain=supporting, degree=1, confidence=medium)
- **玻璃基板** [material] (value_chain=upstream, degree=1, confidence=high)
- **短板材料** [material] (value_chain=upstream, degree=0, confidence=medium)
- **薄膜晶体管液晶显示(TFT-LCD)** [technology] (value_chain=supporting, degree=1, confidence=medium)
- **薄膜晶体管液晶显示器件** [product] (value_chain=unknown, degree=1, confidence=medium)
- **配套材料** [material] (value_chain=upstream, degree=0, confidence=medium)
- **靶材** [material] (value_chain=upstream, degree=0, confidence=medium)

## auxiliary (4 个实体)

- **产业和专项政策** [policy] (value_chain=supporting, degree=0, confidence=medium)
- **产业政策环境** [policy] (value_chain=supporting, degree=0, confidence=medium)
- **区域产业集群** [region] (value_chain=supporting, degree=0, confidence=medium)
- **面板制造企业** [company] (value_chain=supporting, degree=0, confidence=medium)

## 主要候选关系摘要

| 主语 | 关系 | 宾语 | 证据数 | 页码 | 置信 |
|---|---|---|---|---|---|
| 薄膜晶体管液晶显示(TFT-LCD) | PART_OF | 新型显示技术 | 1 | 4 | medium |
| 主动矩阵有机发光二极管显示(AMOLED) | PART_OF | 新型显示技术 | 1 | 4 | medium |
| 微型发光二极管显示(Micro-LED) | PART_OF | 新型显示技术 | 1 | 4 | medium |
| 激光显示 | PART_OF | 新型显示技术 | 1 | 4 | medium |
| 新型显示材料 | PART_OF | 先进电子材料 | 1 | 18 | medium |
| 薄膜晶体管液晶显示器件 | PART_OF | 新型显示器件 | 1 | 17 | medium |
| 有机发光二极管显示面板 | PART_OF | 新型显示器件 | 1 | 17 | medium |
| 玻璃基板 | INPUT_TO | 液晶面板 | 1 | 29 | medium |

## 证据页码分布

- 第 4 页: 4 条关系引用
- 第 17 页: 2 条关系引用
- 第 18 页: 1 条关系引用
- 第 29 页: 1 条关系引用

## 证据不足区域

- 下游应用（t09）：证据不足，候选稀少
- 国产替代（t10）：多为趋势/政策表述，非现实产业关系

## 待人工审核重点

- P0: 5 项
- P1: 3 项
- P2: 0 项
- P3: 44 项

## 冲突数: 0


---
> 本草案由阶段 4 脚本自动生成，不得作为最终产业链图谱使用。