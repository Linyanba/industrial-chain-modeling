# 第二阶段结构化证据库构建报告

## 1. 文档与校验

- 文件：D:\产业链建模\data\raw_pdf\中国半导体白皮书.pdf
- doc_id：中国半导体白皮书_1b73a8b1f91b
- SHA256：1b73a8b1f91b1c20b1bd8fdaff2731477dbac3feed3091c973126e8db0454b54
- 页面总数：25
- parsed_run：D:\产业链建模\parsed_documents\中国半导体白皮书_1b73a8b1f91b_20260713_171812
- 日志：D:\产业链建模\logs\stage2_build_evidence_20260713_171812.log

## 2. 环境与依赖

- Python: 3.10.20
- PyMuPDF: 1.28.0
- Pillow: 12.1.0
- pandas: available
- pdfplumber: missing
- BeautifulSoup4: missing

## 3. 文本块与证据块

- 页面级文本块总数：734
- 证据块总数：256
- 高优先级证据块数量：74

### 证据块类型统计

- caption: 15
- heading: 95
- paragraph: 137
- table: 9

## 4. 章节识别

- 识别章节数：36
- 局限：章节识别基于原生文本块、标题编号、字体和加粗估计；目录页不作为正文结构事实；少量图文混排页仍需人工核验阅读顺序。

## 5. 表格处理

- 成功提取表格数：9
- 待复核表格页：8, 9, 12, 13, 15, 17, 19, 20, 21
- Page 8 中国半导体白皮书_1b73a8b1f91b_p008_t01: quality=0.47, review=yes
- Page 9 中国半导体白皮书_1b73a8b1f91b_p009_t01: quality=0.47, review=yes
- Page 12 中国半导体白皮书_1b73a8b1f91b_p012_t01: quality=0.61, review=yes
- Page 13 中国半导体白皮书_1b73a8b1f91b_p013_t01: quality=0.48, review=yes
- Page 15 中国半导体白皮书_1b73a8b1f91b_p015_t01: quality=0.45, review=yes
- Page 17 中国半导体白皮书_1b73a8b1f91b_p017_t01: quality=1.00, review=yes
- Page 19 中国半导体白皮书_1b73a8b1f91b_p019_t01: quality=0.56, review=yes
- Page 20 中国半导体白皮书_1b73a8b1f91b_p020_t01: quality=0.73, review=yes
- Page 21 中国半导体白皮书_1b73a8b1f91b_p021_t01: quality=0.67, review=yes

## 6. 图表与产业链图

- 图表归档记录数：16
- 产业链图归档：Page 9, Page 14
- 已确认的产业链图仅作为待复核视觉证据，不自动识别节点、箭头、上下游关系或产业层级。

## 7. 当前解析风险

- 表格结构由 PyMuPDF 原生表格检测获得，复杂图表式表格仍需人工核验行列和表头。
- 图表页只保留图题、周边文字和页面图，不自动还原全部数据点。
- 产业链图仅归档为视觉证据，第三阶段前必须人工确认节点、箭头方向和文本边界。

## 8. 第三阶段输入建议

- 优先输入 evidence_chunks.jsonl 中 stage3_priority = high 的正文证据块。
- 优先输入 tables\ 中 parse_quality 较高且 review_required = false 的表格。
- 已确认的产业链图仅作为待复核视觉证据，不可直接作为关系事实写入图谱。

## 9. 待人工复核

- Page 8 table: 表格结构需人工复核：行数=11，常见列数=7，填充率=0.04，列数一致性=1.00 (high)
- Page 9 table: 表格结构需人工复核：行数=10，常见列数=10，填充率=0.03，列数一致性=1.00 (high)
- Page 12 table: 表格结构需人工复核：行数=13，常见列数=15，填充率=0.28，列数一致性=1.00 (medium)
- Page 13 table: 表格结构需人工复核：行数=14，常见列数=9，填充率=0.05，列数一致性=1.00 (high)
- Page 15 table: 表格结构需人工复核：行数=2，常见列数=2，填充率=0.00，列数一致性=1.00 (high)
- Page 17 table: 表格结构需人工复核：行数=1，常见列数=3，填充率=1.00，列数一致性=1.00 (medium)
- Page 19 table: 表格结构需人工复核：行数=19，常见列数=12，填充率=0.19，列数一致性=1.00 (medium)
- Page 20 table: 表格结构需人工复核：行数=7，常见列数=2，填充率=0.50，列数一致性=1.00 (medium)
- Page 21 table: 表格结构需人工复核：行数=5，常见列数=4，填充率=0.40，列数一致性=1.00 (medium)
- Page 9 chain_diagram: 产业链图仅归档为视觉证据，节点、箭头方向和关系需人工复核 (high)
- Page 14 chain_diagram: 产业链图仅归档为视觉证据，节点、箭头方向和关系需人工复核 (high)
- Page 1 text: 第一阶段文本质量标记为 low (medium)
- Page 2 text: 第一阶段文本质量标记为 low (medium)
- Page 3 text: 第一阶段文本质量标记为 low (medium)
- Page 4 text: 第一阶段文本质量标记为 low (medium)
- Page 7 text: 检测到 15 个疑似PDF字体映射乱码文本块 (medium)
- Page 7 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 8 text: 检测到 7 个疑似PDF字体映射乱码文本块 (medium)
- Page 8 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 9 text: 检测到 36 个疑似PDF字体映射乱码文本块 (medium)
- Page 11 text: 检测到 13 个疑似PDF字体映射乱码文本块 (medium)
- Page 11 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 12 text: 检测到 53 个疑似PDF字体映射乱码文本块 (medium)
- Page 12 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 13 text: 检测到 6 个疑似PDF字体映射乱码文本块 (medium)
- Page 13 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 14 text: 检测到 28 个疑似PDF字体映射乱码文本块 (medium)
- Page 15 text: 检测到 8 个疑似PDF字体映射乱码文本块 (medium)
- Page 15 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 17 text: 检测到 22 个疑似PDF字体映射乱码文本块 (medium)

## 11. 验证结果

- 原始PDF_SHA256_运行前后相同: True
- document_structure_json存在: True
- 页面记录覆盖全文且无缺页: True
- evidence_chunks_jsonl非空: True
- 每个evidence_id唯一: True
- 每个证据块页码在范围内: True
- 每个证据块能定位source_block_ids: True
- 表格manifest输出路径存在或已标记unresolved: True
- 已确认产业链图均有归档记录: True
- 未调用大模型: True
- 未生成产业关系: True

## 12. 阶段边界声明

- 本阶段未调用大模型、未做OCR、未抽取产业链关系、未生成产业链结论。
