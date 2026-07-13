# 保守版产业链草案

> **声明**：本文件是极少人工审核模式下的保守草案，**不是最终产业链图谱**。
> 被 auto_defer 或 archive 的内容不是永久删除，只是暂时不进入保守草案。
> 如需更高召回率，可从 archived_low_priority_items.csv 回捞。

---

## 一、保留实体列表

共 39 个实体进入保守草案：

| 编号 | 名称 | 类型 | 层级 | 价值链位置 | 置信度 |
|------|------|------|------|------------|--------|
| CE0002 | 新型显示产业 | industry | L0_industry | cross_stage | high |
| CE0004 | 显示材料 | material | L4_object | upstream | high |
| CE0005 | 配套材料 | material | L4_object | upstream | medium |
| CE0007 | 产业链协同 | industry | L0_industry | cross_stage | medium |
| CE0009 | 创新链 | industry_link | L3_industry_link | unknown | medium |
| CE0010 | 短板材料 | material | L4_object | upstream | medium |
| CE0011 | 核心技术 | technology | L4_object | supporting | medium |
| CE0012 | 关键原材料 | material | L4_object | upstream | medium |
| CE0013 | OLED 材料 | material | L4_object | upstream | medium |
| CE0014 | 显示芯片 | material | L4_object | upstream | medium |
| CE0015 | 掩模版 | material | L4_object | upstream | high |
| CE0016 | 靶材 | material | L4_object | upstream | medium |
| CE0017 | 玻璃基板 | material | L4_object | upstream | high |
| CE0018 | 液晶材料 | material | L4_object | upstream | high |
| CE0019 | 上下游 | industry_link | L3_industry_link | unknown | medium |
| CE0020 | 材料 | material | L4_object | upstream | medium |
| CE0021 | 工艺 | process | L4_object | midstream | medium |
| CE0022 | 产品 | product | L4_object | unknown | medium |
| CE0023 | 偏光片 | product | L4_object | unknown | medium |
| CE0024 | 有机发光材料 | product | L4_object | unknown | medium |
| CE0025 | 全面屏手机 | product | L4_object | unknown | medium |
| CE0026 | 4K/8K 电视 | product | L4_object | unknown | medium |
| CE0027 | AR/VR | application | L4_object | downstream | medium |
| CE0028 | 可穿戴设备 | application | L4_object | downstream | medium |
| CE0029 | 显示面板 | product | L4_object | unknown | high |
| CE0030 | 湿电子化学品 | material | L4_object | upstream | medium |
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
| CE0042 | 液晶面板 | product | L4_object | unknown | medium |
| CE0043 | 产业生态 | industry_link | L3_industry_link | unknown | medium |

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

- 归档总数：45 条
  - auto_archive_rejected: 44
  - auto_defer: 0
  - auto_reject_as_fact: 1

## 六、最小人工审核问题

共 7 条需人工确认：

**MHR0001** [P0] 关系候选待复核: 新型显示产业->SUPPLIES_TO->产业链协同
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P0 结构性问题需人工确认

**MHR0002** [P0] 关系候选待复核: 产业链->SUPPLIES_TO->创新链
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P0 结构性问题需人工确认

**MHR0003** [P1] 关系候选待复核: 关键原材料->INPUT_TO->显示面板企业
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P1 具体产业内容或结构关系，交由人工/本地LLM审核

**MHR0004** [P0] 关系候选待复核: 产业链->INPUT_TO->上下游
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P0 结构性问题需人工确认

**MHR0005** [P1] 关系候选待复核: 材料->INPUT_TO->工艺
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P1 具体产业内容或结构关系，交由人工/本地LLM审核

**MHR0006** [P0] 关系候选待复核: 供应链->OUTPUT_OF->显示产业
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P0 结构性问题需人工确认

**MHR0007** [P0] 关系候选待复核: 创新链->INPUT_TO->产业链
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P0 结构性问题需人工确认

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