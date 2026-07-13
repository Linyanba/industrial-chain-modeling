# 第二阶段结构化证据库构建报告

## 1. 文档与校验

- 文件：D:\产业链建模\data\raw_pdf\新型显示白皮书.pdf
- doc_id：新型显示白皮书_7f4f945d7c21
- SHA256：7f4f945d7c21e1b10c89aeb1bd6ea2db502c166ae6394d9294a785f7eec6dd6a
- 页面总数：36
- parsed_run：D:\产业链建模\parsed_documents\新型显示白皮书_7f4f945d7c21_20260713_141503
- 日志：D:\产业链建模\logs\stage2_build_evidence_20260713_141503.log

## 2. 环境与依赖

- Python: 3.10.20
- PyMuPDF: 1.28.0
- Pillow: 12.1.0
- pandas: available
- pdfplumber: missing
- BeautifulSoup4: missing

## 3. 文本块与证据块

- 页面级文本块总数：654
- 证据块总数：199
- 高优先级证据块数量：44

### 证据块类型统计

- heading: 74
- paragraph: 119
- table: 4
- table_candidate_unparsed: 2

## 4. 章节识别

- 识别章节数：36
- 局限：章节识别基于原生文本块、标题编号、字体和加粗估计；目录页不作为正文结构事实；少量图文混排页仍需人工核验阅读顺序。

## 5. 表格处理

- 成功提取表格数：4
- 待复核表格页：14, 15, 24, 25
- Page 14 新型显示白皮书_7f4f945d7c21_p014_t01: quality=1.00, review=yes
- Page 15 新型显示白皮书_7f4f945d7c21_p015_t01: quality=1.00, review=yes
- Page 24 新型显示白皮书_7f4f945d7c21_p024_t01: quality=0.96, review=yes
- Page 25 新型显示白皮书_7f4f945d7c21_p025_t01: quality=0.82, review=yes

## 6. 图表与产业链图

- 图表归档记录数：13
- 产业链图归档：无
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

- Page 13 table: 表格候选页未检测到可输出的结构化表格 (medium)
- Page 14 table: 表格结构需人工复核：行数=4，常见列数=2，填充率=1.00，列数一致性=1.00 (medium)
- Page 15 table: 表格结构需人工复核：行数=9，常见列数=3，填充率=1.00，列数一致性=1.00 (medium)
- Page 23 table: 表格候选页未检测到可输出的结构化表格 (medium)
- Page 24 table: 表格结构需人工复核：行数=3，常见列数=5，填充率=0.93，列数一致性=1.00 (medium)
- Page 25 table: 表格结构需人工复核：行数=2，常见列数=3，填充率=0.67，列数一致性=1.00 (medium)
- Page 1 text: 第一阶段文本质量标记为 low (medium)
- Page 6 text: 第一阶段文本质量标记为 low (medium)
- Page 7 text: 第一阶段文本质量标记为 low (medium)
- Page 8 text: 第一阶段文本质量标记为 low (medium)
- Page 9 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 10 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 12 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 13 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 14 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 15 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 20 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 21 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 22 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 23 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 24 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 25 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 26 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 27 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 28 layout: 图表/表格/混排页面，阅读顺序或结构需抽样核验 (medium)
- Page 36 text: 第一阶段文本质量标记为 low (medium)

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
