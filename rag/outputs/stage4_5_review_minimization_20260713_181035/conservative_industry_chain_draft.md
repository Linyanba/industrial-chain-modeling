# 保守版产业链草案

> **声明**：本文件是极少人工审核模式下的保守草案，**不是最终产业链图谱**。
> 被 auto_defer 或 archive 的内容不是永久删除，只是暂时不进入保守草案。
> 如需更高召回率，可从 archived_low_priority_items.csv 回捞。

---

## 一、保留实体列表

共 40 个实体进入保守草案：

| 编号 | 名称 | 类型 | 层级 | 价值链位置 | 置信度 |
|------|------|------|------|------------|--------|
| CE0002 | 产业链上下游 | industry_link | L3_industry_link | unknown | medium |
| CE0003 | 新型显示产业 | industry | L0_industry | cross_stage | high |
| CE0004 | 显示材料 | material | L4_object | upstream | high |
| CE0006 | 配套材料 | material | L4_object | upstream | medium |
| CE0007 | 显示器件 | product | L4_object | unknown | medium |
| CE0009 | 显示材料产业 | sub_chain | L2_sub_chain | cross_stage | medium |
| CE0010 | 关键原材料 | material | L4_object | upstream | medium |
| CE0011 | 玻璃基板 | material | L4_object | upstream | high |
| CE0012 | OLED 材料 | material | L4_object | upstream | high |
| CE0013 | 显示芯片 | material | L4_object | upstream | high |
| CE0014 | 掩模版 | material | L4_object | upstream | high |
| CE0015 | 靶材 | material | L4_object | upstream | high |
| CE0016 | 液晶材料 | material | L4_object | upstream | high |
| CE0017 | 上下游 | industry_link | L3_industry_link | unknown | medium |
| CE0018 | 加工 | process | L4_object | midstream | medium |
| CE0019 | 采购 | industry_link | L3_industry_link | unknown | medium |
| CE0020 | 营销 | industry_link | L3_industry_link | unknown | medium |
| CE0021 | 物流 | industry_link | L3_industry_link | unknown | medium |
| CE0022 | 彩色滤光片 | material | L4_object | upstream | medium |
| CE0023 | 湿电子化学品 | material | L4_object | upstream | medium |
| CE0024 | 光刻胶 | material | L4_object | upstream | medium |
| CE0025 | 有机发光材料 | product | L4_object | unknown | medium |
| CE0026 | 全面屏手机 | product | L4_object | unknown | medium |
| CE0027 | 4K/8K 电视 | product | L4_object | unknown | medium |
| CE0028 | AR/VR | application | L4_object | downstream | medium |
| CE0029 | 可穿戴设备 | application | L4_object | downstream | medium |
| CE0030 | 显示面板 | product | L4_object | unknown | high |
| CE0031 | 新型显示技术 | technology | L4_object | supporting | medium |
| CE0032 | 薄膜晶体管液晶显示(TFT-LCD) | technology | L4_object | supporting | medium |
| CE0033 | 主动矩阵有机发光二极管显示(AMOLED) | technology | L4_object | supporting | medium |
| CE0034 | 微型发光二极管显示(Micro-LED) | technology | L4_object | supporting | medium |
| CE0035 | 激光显示 | technology | L4_object | supporting | medium |
| CE0036 | 先进电子材料 | material | L4_object | upstream | medium |
| CE0037 | 新型显示材料 | material | L4_object | upstream | high |
| CE0038 | 新型显示器件 | product | L4_object | unknown | medium |
| CE0039 | 薄膜晶体管液晶显示器件 | product | L4_object | unknown | medium |
| CE0040 | 有机发光二极管显示面板 | product | L4_object | unknown | medium |
| CE0041 | 新型显示产业链 | industry | L0_industry | cross_stage | medium |
| CE0043 | 液晶面板 | product | L4_object | unknown | medium |
| CE0044 | 产业生态 | industry_link | L3_industry_link | unknown | medium |

## 二、保留关系列表

共 8 条关系进入保守草案：

| 编号 | 主体 | 关系 | 客体 | 证据页 | 置信度 |
|------|------|------|------|--------|--------|
| NR0001 | 薄膜晶体管液晶显示(TFT-LCD) | PART_OF | 新型显示技术 | 4 | medium |
| NR0002 | 主动矩阵有机发光二极管显示(AMOLED) | PART_OF | 新型显示技术 | 4 | medium |
| NR0003 | 微型发光二极管显示(Micro-LED) | PART_OF | 新型显示技术 | 4 | medium |
| NR0004 | 激光显示 | PART_OF | 新型显示技术 | 4 | medium |
| NR0005 | 新型显示材料 | PART_OF | 先进电子材料 | 18 | medium |
| NR0006 | 薄膜晶体管液晶显示器件 | PART_OF | 新型显示器件 | 17 | medium |
| NR0007 | 有机发光二极管显示面板 | PART_OF | 新型显示器件 | 17 | medium |
| NR0008 | 玻璃基板 | INPUT_TO | 液晶面板 | 29 | medium |

## 三、自动合并建议

共 0 条别名合并建议：


## 四、自动关系修正建议

共 0 条关系修正建议：


## 五、被暂缓/归档统计

- 归档总数：61 条
  - auto_archive_rejected: 61
  - auto_defer: 0
  - auto_reject_as_fact: 0

## 六、最小人工审核问题

共 7 条需人工确认：

**MHR0001** [P0] 关系候选待复核: 产业链->INPUT_TO->产业链上下游
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P0 结构性问题需人工确认

**MHR0002** [P0] 关系候选待复核: 新型显示产业->SUPPLIES_TO->产业链
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P0 结构性问题需人工确认

**MHR0003** [P1] 关系候选待复核: 面板制造企业->USES_TECHNOLOGY->配套材料
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P1 具体产业内容或结构关系，交由人工/本地LLM审核

**MHR0004** [P1] 关系候选待复核: 显示材料产业->SUPPLIES_TO->产业集群
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P1 具体产业内容或结构关系，交由人工/本地LLM审核

**MHR0005** [P1] 关系候选待复核: 关键原材料->INPUT_TO->显示面板
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P1 具体产业内容或结构关系，交由人工/本地LLM审核

**MHR0006** [P0] 关系候选待复核: 产业链->INPUT_TO->上下游
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P0 结构性问题需人工确认

**MHR0007** [P1] 关系候选待复核: 玻璃基板->OUTPUT_OF->高世代玻璃基板
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P1 具体产业内容或结构关系，交由人工/本地LLM审核

## 七、证据页码分布

保守关系证据涉及页码：17, 18, 29, 4

## 八、风险说明

1. 本草案采用**高精度低召回**策略，牺牲了部分候选关系的覆盖面。
2. 被暂缓/归档的内容可通过 `archived_low_priority_items.csv` 回捞。
3. 所有保留关系均有原文 quote 证据支撑，可追溯到阶段 3C/4。
4. 本阶段不注入任何固定行业骨架，只继承当前 PDF 的证据候选。
5. 最终树状结构由后续 document_driven 模板根据当前实体、关系和证据生成。

---
*生成时间：自动生成，阶段 4.5 保守模式*