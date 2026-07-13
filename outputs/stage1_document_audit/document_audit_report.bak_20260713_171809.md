# 第一阶段文档审计报告

## 1. 文档基本信息

- 文件：D:\产业链建模\data\raw_pdf\新型显示白皮书.pdf
- doc_id：新型显示白皮书_7f4f945d7c21
- SHA256：7f4f945d7c21e1b10c89aeb1bd6ea2db502c166ae6394d9294a785f7eec6dd6a
- 文件大小 MB：1.41
- 标题：unknown
- 发布机构：cesirohsckw
- 发布日期：unknown
- 地域范围：unknown
- PDF 元数据标题：unknown
- PDF 元数据作者：cesirohsckw
- PDF 创建日期：2019-08-09
- 是否加密：no
- 日志：D:\产业链建模\logs\stage1_document_audit_20260713_141230.log

## 2. 环境与依赖

- Python: 3.10.20
- PyMuPDF: 1.28.0
- Pillow: 12.1.0
- pandas: available
- pdfplumber: missing

## 3. 原生文本可用性

- PDF 是否可直接提取文本：是
- 可直接用原生文本解析的页面比例：31/36 (86.1%)
- 需要或可能需要 OCR 的页面：无

## 4. 页面总数与类型统计

- 页面总数：36
- chart: 9
- cover: 1
- mixed: 4
- paragraph: 16
- table: 2
- unknown: 4

## 5. 候选页面列表

- 表格候选页：13, 14, 15, 23, 24, 25
- 图表候选页：9, 10, 12, 13, 20, 21, 22, 23, 24, 25, 26, 27, 28
- 产业链图候选页：无
- 产业链图视觉确认页：无
- 仍需人工复核页：1, 6, 7, 8, 9, 10, 12, 13, 14, 15, 20, 21, 22, 23, 24, 25, 26, 27, 28, 36

## 6. 高价值页面

- Page 9: high；关键词命中：产业链/上游/中游/下游/制造/应用；图表候选；下一步：manual_inspection
- Page 33: high；关键词命中：产业链/价值链/下游/产业生态/应用；下一步：extract_native_text
- Page 7: high；关键词命中：产业链/供应链/下游/产业生态；下一步：extract_native_text
- Page 12: high；关键词命中：产业链/价值链/供应链；图表候选；下一步：manual_inspection
- Page 11: high；关键词命中：产业链/下游/产业生态；下一步：extract_native_text
- Page 10: medium；关键词命中：产业链/产业生态；图表候选；下一步：manual_inspection
- Page 3: medium；关键词命中：下游/产业生态；下一步：extract_native_text
- Page 4: medium；关键词命中：产业链/上游；下一步：extract_native_text
- Page 34: medium；关键词命中：产业链/上游；下一步：extract_native_text
- Page 16: medium；关键词命中：产业链/制造/应用；下一步：extract_native_text
- Page 27: medium；关键词命中：产业链；图表候选；下一步：manual_inspection
- Page 28: medium；关键词命中：下游；图表候选；下一步：manual_inspection
- Page 30: medium；关键词命中：供应链/应用；下一步：extract_native_text
- Page 32: medium；关键词命中：下游/应用；下一步：extract_native_text
- Page 35: medium；关键词命中：产业结构/应用；下一步：extract_native_text
- Page 6: low；关键词命中：产业链；下一步：extract_native_text
- Page 13: low；表格候选；图表候选；下一步：extract_table_structure
- Page 15: low；关键词命中：应用；表格候选；下一步：extract_table_structure
- Page 23: low；表格候选；图表候选；下一步：extract_table_structure
- Page 24: low；表格候选；图表候选；下一步：extract_table_structure
- Page 25: low；表格候选；图表候选；下一步：extract_table_structure
- Page 31: low；关键词命中：下游；下一步：extract_native_text
- Page 2: low；关键词命中：制造/应用；下一步：extract_native_text
- Page 14: low；表格候选；下一步：extract_table_structure
- Page 17: low；关键词命中：制造/应用；下一步：extract_native_text
- Page 18: low；关键词命中：应用；下一步：extract_native_text
- Page 20: low；图表候选；下一步：manual_inspection
- Page 21: low；图表候选；下一步：manual_inspection
- Page 22: low；图表候选；下一步：manual_inspection
- Page 26: low；图表候选；下一步：manual_inspection

## 7. 主要解析风险

- 表格、统计图表和产业链图候选均来自启发式检测或抽样视觉复核，第二阶段不能直接把候选图认定为真实产业链关系。
- 多栏或图文混排页面需要保留文本块位置并校验阅读顺序。
- 图表页优先抽取图题、图注、坐标轴和结论文本；不应在缺少视觉核验的情况下自动推断完整数值。
- 原生文本字符数低或疑似扫描页需要 OCR 后再做版面恢复，并进行人工抽样核验。

## 8. 第二阶段推荐解析路由

- 普通原生文本页 → PyMuPDF 原生文本提取，并保留页码与文本块位置
- 复杂多栏文本页 → 原生文本 + 版面顺序校验
- 表格页 → 表格结构解析，保留表题、表头、单位、脚注和页码
- 产业链图候选页 → 图像视觉识别 + 人工审核箭头方向与节点名称
- 扫描或乱码页 → OCR + 版面恢复 + 人工抽样核验
- 普通统计图表页 → 优先提取图题、图注、坐标轴和结论，不直接自动推断全部数值

## 9. 验证结果

- documents.csv包含本PDF记录: True
- page_manifest.csv行数等于PDF页数: True
- 页码连续且无缺失: True
- 所有渲染页PNG存在: True
- high_value_pages.csv已生成: True
- sample_review.csv已生成: True
- document_audit_report.md已生成: True
- CSV使用UTF-8-SIG写入: True
- PDF哈希前后一致: True
- 未执行OCR/LLM/关系抽取: True
