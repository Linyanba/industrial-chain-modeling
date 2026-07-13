# 层级树 refinement 报告

- 优化前节点/边: 43 / 42
- 优化后节点/边: 43 / 42
- 优化后最大深度: 3
- 一级节点: 晶圆制造, 计算芯片, 半导体产品与应用, 半导体材料, 半导体核心环节, 半导体技术, 半导体设备
- LLM建议: 未使用或不可用，规则法fallback
- LLM原始建议hash: N/A

## 新增二级/三级节点
- 无

## 父子层级修复
- 芯片: 半导体产品与应用 -> AI推理芯片 (composite_parent_fix)
- 设计: 半导体核心环节 -> 半导体设计制造 (composite_parent_fix)
- EDA: 半导体技术 -> EDA软件 (composite_parent_fix)

## 删除节点
- 无

## 证据与展示节点
- 带evidence_ids节点: 40
- display_schema_node节点: 6

## weak_branch
- 无

## 结构改进说明
refinement 将同层的组合词/组成部分改为父子层级，并为材料、设备、工艺等平铺分支增加中间分组节点，使结构更接近“核心模块→子领域/环节→具体对象”的多级产业链树。

## 程序校验: 通过
- issues: []