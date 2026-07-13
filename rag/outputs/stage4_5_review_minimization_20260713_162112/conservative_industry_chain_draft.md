# 保守版产业链草案

> **声明**：本文件是极少人工审核模式下的保守草案，**不是最终产业链图谱**。
> 被 auto_defer 或 archive 的内容不是永久删除，只是暂时不进入保守草案。
> 如需更高召回率，可从 archived_low_priority_items.csv 回捞。

---

## 一、保留实体列表

共 37 个实体进入保守草案：

| 编号 | 名称 | 类型 | 层级 | 价值链位置 | 置信度 |
|------|------|------|------|------------|--------|
| CE0001 | 设计 | process | L4_object | midstream | medium |
| CE0002 | 代工 | process | L4_object | midstream | medium |
| CE0003 | 封装 | industry_link | L3_industry_link | midstream | high |
| CE0005 | EDA | technology | L4_object | supporting | medium |
| CE0006 | 计算芯片 | unknown | unknown | unknown | medium |
| CE0007 | 汽车半导体 | product | L4_object | unknown | medium |
| CE0008 | 先进封装技术 | technology | L4_object | supporting | medium |
| CE0009 | 2.5D/3D先进封装 | technology | L4_object | supporting | medium |
| CE0010 | 设备 | equipment | L4_object | upstream | medium |
| CE0011 | 原材料 | material | L4_object | upstream | medium |
| CE0012 | EDA软件 | technology | L4_object | supporting | medium |
| CE0013 | 硅片 | material | L4_object | upstream | medium |
| CE0014 | 金属 | material | L4_object | upstream | medium |
| CE0015 | 化学品 | material | L4_object | upstream | medium |
| CE0016 | 气体 | material | L4_object | upstream | medium |
| CE0017 | 封装材料 | material | L4_object | upstream | medium |
| CE0018 | 基材 | material | L4_object | upstream | medium |
| CE0019 | 光刻机 | equipment | L4_object | upstream | medium |
| CE0020 | 晶圆缺陷监测系统 | equipment | L4_object | upstream | medium |
| CE0021 | 封测 | industry_link | L3_industry_link | unknown | medium |
| CE0022 | 芯片 | product | L4_object | unknown | medium |
| CE0023 | OSAT | industry_link | L3_industry_link | unknown | medium |
| CE0026 | 集成电路 | product | L4_object | unknown | medium |
| CE0027 | 光电子器件 | product | L4_object | unknown | medium |
| CE0028 | 分立器件 | product | L4_object | unknown | medium |
| CE0029 | 传感器 | product | L4_object | unknown | medium |
| CE0030 | 封装和测试环节 | industry_link | L3_industry_link | midstream | medium |
| CE0031 | GPU | unknown | unknown | unknown | medium |
| CE0032 | 晶圆制造 | process | L4_object | midstream | medium |
| CE0033 | 沉积 | process | L4_object | midstream | medium |
| CE0034 | 光刻 | process | L4_object | midstream | medium |
| CE0035 | 刻蚀 | process | L4_object | midstream | medium |
| CE0036 | 离子注入 | process | L4_object | midstream | medium |
| CE0037 | ASIC | unknown | unknown | unknown | medium |
| CE0038 | 硅晶圆 | material | L4_object | upstream | medium |
| CE0039 | 沉积设备 | equipment | L4_object | upstream | medium |
| CE0040 | AI推理芯片 | product | L4_object | unknown | medium |

## 二、保留关系列表

共 6 条关系进入保守草案：

| 编号 | 主体 | 关系 | 客体 | 证据页 | 置信度 |
|------|------|------|------|--------|--------|
| NR0001 | 沉积 | PART_OF | 晶圆制造 | 10 | medium |
| NR0002 | 光刻 | PART_OF | 晶圆制造 | 10 | medium |
| NR0003 | 刻蚀 | PART_OF | 晶圆制造 | 10 | medium |
| NR0004 | 离子注入 | PART_OF | 晶圆制造 | 10 | medium |
| NR0009 | GPU | PART_OF | 计算芯片 | 22 | medium |
| NR0010 | ASIC | PART_OF | 计算芯片 | 22 | medium |

## 三、自动合并建议

共 0 条别名合并建议：


## 四、自动关系修正建议

共 0 条关系修正建议：


## 五、被暂缓/归档统计

- 归档总数：121 条
  - auto_archive_rejected: 121
  - auto_defer: 0
  - auto_reject_as_fact: 0

## 六、最小人工审核问题

共 7 条需人工确认：

**MHR0001** [P1] 关系候选待复核: 设备->INPUT_TO->制造集成电路（IC）、光电子器件、分立器件、传感器、芯片
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P1 具体产业内容或结构关系，交由人工/本地LLM审核

**MHR0002** [P1] 关系候选待复核: 原材料->INPUT_TO->制造集成电路（IC）、光电子器件、分立器件、传感器、芯片
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P1 具体产业内容或结构关系，交由人工/本地LLM审核

**MHR0003** [P1] 关系候选待复核: EDA软件->INPUT_TO->制造集成电路（IC）、光电子器件、分立器件、传感器、芯片
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P1 具体产业内容或结构关系，交由人工/本地LLM审核

**MHR0004** [P1] 关系候选待复核: 硅片->INPUT_TO->制造集成电路（IC）、光电子器件、分立器件、传感器、芯片
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P1 具体产业内容或结构关系，交由人工/本地LLM审核

**MHR0005** [P1] 关系候选待复核: 金属->INPUT_TO->制造集成电路（IC）、光电子器件、分立器件、传感器、芯片
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P1 具体产业内容或结构关系，交由人工/本地LLM审核

**MHR0006** [P1] 关系候选待复核: 化学品->INPUT_TO->制造集成电路（IC）、光电子器件、分立器件、传感器、芯片
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P1 具体产业内容或结构关系，交由人工/本地LLM审核

**MHR0007** [P1] 关系候选待复核: 气体->INPUT_TO->制造集成电路（IC）、光电子器件、分立器件、传感器、芯片
  - 建议选项：保留|暂缓|删除|调整名称或层级
  - 原因：P1 具体产业内容或结构关系，交由人工/本地LLM审核

## 七、证据页码分布

保守关系证据涉及页码：10, 22

## 八、风险说明

1. 本草案采用**高精度低召回**策略，牺牲了部分候选关系的覆盖面。
2. 被暂缓/归档的内容可通过 `archived_low_priority_items.csv` 回捞。
3. 所有保留关系均有原文 quote 证据支撑，可追溯到阶段 3C/4。
4. 本阶段不注入任何固定行业骨架，只继承当前 PDF 的证据候选。
5. 最终树状结构由后续 document_driven 模板根据当前实体、关系和证据生成。

---
*生成时间：自动生成，阶段 4.5 保守模式*