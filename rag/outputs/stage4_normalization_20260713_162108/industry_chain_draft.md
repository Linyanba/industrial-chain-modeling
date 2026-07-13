# 产业链草案（待人工审核）

> **重要声明**：本文档为阶段 4 自动生成的产业链结构草案，所有实体和关系均为待审核候选，**不得视为最终事实**。尚未完成人工确认、尚未生成正式图谱、尚未写入 Neo4j。

## L3_industry_link (5 个实体)

- **OSAT** [industry_link] (value_chain=unknown, degree=0, confidence=medium)
- **封测** [industry_link] (value_chain=unknown, degree=0, confidence=medium)
- **封装** [industry_link] (value_chain=midstream, degree=1, confidence=high)
- **封装和测试环节** [industry_link] (value_chain=midstream, degree=0, confidence=medium)
- **测试** [industry_link] (value_chain=midstream, degree=1, confidence=high)

## L4_object (31 个实体)

- **2.5D/3D先进封装** [technology] (value_chain=supporting, degree=0, confidence=medium)
- **AI推理芯片** [product] (value_chain=unknown, degree=0, confidence=medium)
- **EDA** [technology] (value_chain=supporting, degree=0, confidence=medium)
- **EDA软件** [technology] (value_chain=supporting, degree=0, confidence=medium)
- **代工** [process] (value_chain=midstream, degree=1, confidence=medium)
- **传感器** [product] (value_chain=unknown, degree=0, confidence=medium)
- **先进封装技术** [technology] (value_chain=supporting, degree=0, confidence=medium)
- **光刻** [process] (value_chain=midstream, degree=1, confidence=medium)
- **光刻机** [equipment] (value_chain=upstream, degree=0, confidence=medium)
- **光电子器件** [product] (value_chain=unknown, degree=0, confidence=medium)
- **分立器件** [product] (value_chain=unknown, degree=0, confidence=medium)
- **刻蚀** [process] (value_chain=midstream, degree=1, confidence=medium)
- **化学品** [material] (value_chain=upstream, degree=0, confidence=medium)
- **半导体生态** [process] (value_chain=midstream, degree=4, confidence=low)
- **原材料** [material] (value_chain=upstream, degree=0, confidence=medium)
- **基材** [material] (value_chain=upstream, degree=0, confidence=medium)
- **封装材料** [material] (value_chain=upstream, degree=0, confidence=medium)
- **晶圆制造** [process] (value_chain=midstream, degree=4, confidence=medium)
- **晶圆缺陷监测系统** [equipment] (value_chain=upstream, degree=0, confidence=medium)
- **气体** [material] (value_chain=upstream, degree=0, confidence=medium)
- **汽车半导体** [product] (value_chain=unknown, degree=0, confidence=medium)
- **沉积** [process] (value_chain=midstream, degree=1, confidence=medium)
- **沉积设备** [equipment] (value_chain=upstream, degree=0, confidence=medium)
- **硅晶圆** [material] (value_chain=upstream, degree=0, confidence=medium)
- **硅片** [material] (value_chain=upstream, degree=0, confidence=medium)
- **离子注入** [process] (value_chain=midstream, degree=1, confidence=medium)
- **芯片** [product] (value_chain=unknown, degree=0, confidence=medium)
- **设备** [equipment] (value_chain=upstream, degree=0, confidence=medium)
- **设计** [process] (value_chain=midstream, degree=1, confidence=medium)
- **金属** [material] (value_chain=upstream, degree=0, confidence=medium)
- **集成电路** [product] (value_chain=unknown, degree=0, confidence=medium)

## auxiliary (2 个实体)

- **安靠科技** [company] (value_chain=supporting, degree=0, confidence=medium)
- **日月光集团** [company] (value_chain=supporting, degree=0, confidence=medium)

## unknown (3 个实体)

- **ASIC** [unknown] (value_chain=unknown, degree=1, confidence=medium)
- **GPU** [unknown] (value_chain=unknown, degree=1, confidence=medium)
- **计算芯片** [unknown] (value_chain=unknown, degree=2, confidence=medium)

## 主要候选关系摘要

| 主语 | 关系 | 宾语 | 证据数 | 页码 | 置信 |
|---|---|---|---|---|---|
| 沉积 | PART_OF | 晶圆制造 | 1 | 10 | medium |
| 光刻 | PART_OF | 晶圆制造 | 1 | 10 | medium |
| 刻蚀 | PART_OF | 晶圆制造 | 1 | 10 | medium |
| 离子注入 | PART_OF | 晶圆制造 | 1 | 10 | medium |
| 设计 | PART_OF | 半导体生态 | 1 | 10 | medium |
| 代工 | PART_OF | 半导体生态 | 1 | 10 | medium |
| 封装 | PART_OF | 半导体生态 | 1 | 10 | medium |
| 测试 | PART_OF | 半导体生态 | 1 | 10 | medium |
| GPU | PART_OF | 计算芯片 | 1 | 22 | medium |
| ASIC | PART_OF | 计算芯片 | 1 | 22 | medium |

## 证据页码分布

- 第 10 页: 8 条关系引用
- 第 22 页: 2 条关系引用

## 证据不足区域

- 下游应用（t09）：证据不足，候选稀少
- 国产替代（t10）：多为趋势/政策表述，非现实产业关系

## 待人工审核重点

- P0: 0 项
- P1: 14 项
- P2: 0 项
- P3: 121 项

## 冲突数: 0


---
> 本草案由阶段 4 脚本自动生成，不得作为最终产业链图谱使用。