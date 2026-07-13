# 第一阶段文档审计报告

## 1. 文档基本信息

- 文件：D:\产业链建模\data\raw_pdf\中国半导体白皮书.pdf
- doc_id：中国半导体白皮书_1b73a8b1f91b
- SHA256：1b73a8b1f91b1c20b1bd8fdaff2731477dbac3feed3091c973126e8db0454b54
- 文件大小 MB：1.93
- 标题：中国半导体白皮书
- 发布机构：unknown
- 发布日期：unknown
- 地域范围：中国
- PDF 元数据标题：unknown
- PDF 元数据作者：unknown
- PDF 创建日期：2022-08-04
- 是否加密：no
- 日志：D:\产业链建模\logs\stage1_document_audit_20260713_171806.log

## 2. 环境与依赖

- Python: 3.10.20
- PyMuPDF: 1.28.0
- Pillow: 12.1.0
- pandas: available
- pdfplumber: missing

## 3. 原生文本可用性

- PDF 是否可直接提取文本：是
- 可直接用原生文本解析的页面比例：20/25 (80.0%)
- 需要或可能需要 OCR 的页面：25

## 4. 页面总数与类型统计

- 页面总数：25
- chain_diagram: 2
- chart: 4
- copyright: 1
- cover: 1
- mixed: 7
- paragraph: 7
- table: 1
- toc: 2

## 5. 候选页面列表

- 表格候选页：8, 9, 12, 13, 15, 17, 19, 20, 21
- 图表候选页：7, 8, 9, 11, 12, 13, 14, 15, 17, 18, 19, 20, 21
- 产业链图候选页：9, 14
- 产业链图视觉确认页：9, 14
- 仍需人工复核页：7, 8, 9, 11, 12, 13, 14, 15, 17, 18, 19, 20, 21, 25

## 6. 高价值页面

- Page 9: high；关键词命中：价值链/上游/下游/设计/制造/封装/应用；表格候选；图表候选；产业链图候选，需人工确认；下一步：visual_review_diagram
- Page 8: high；关键词命中：价值链/上游/下游/设计/制造/封装/应用；表格候选；图表候选；下一步：extract_table_structure
- Page 22: high；关键词命中：产业链/价值链/上游/下游/设计/制造/应用/国产替代；下一步：extract_native_text
- Page 10: high；关键词命中：价值链/上游/下游/设计/制造/封装/测试/应用；下一步：extract_native_text
- Page 14: high；关键词命中：价值链/下游/设计/制造/封装/测试；图表候选；产业链图候选，需人工确认；下一步：visual_review_diagram
- Page 18: high；关键词命中：供应链/上游/下游/设计/应用；图表候选；下一步：manual_inspection
- Page 6: high；关键词命中：上游/下游/设计/制造/封装/应用；下一步：extract_native_text
- Page 3: high；关键词命中：产业链/价值链/下游/设计；下一步：extract_native_text
- Page 23: high；关键词命中：上游/下游/设计/封装；下一步：extract_native_text
- Page 5: medium；关键词命中：产业链/价值链/设计；下一步：extract_native_text
- Page 7: medium；关键词命中：产业链/下游；图表候选；下一步：manual_inspection
- Page 11: medium；关键词命中：上游/设计/制造/应用；图表候选；下一步：manual_inspection
- Page 16: medium；关键词命中：上游/设计/制造/封装/测试；下一步：extract_native_text
- Page 17: medium；关键词命中：供应链/设计；表格候选；图表候选；下一步：extract_table_structure
- Page 13: medium；关键词命中：制造/封装/测试；表格候选；图表候选；下一步：extract_table_structure
- Page 15: medium；关键词命中：设计/制造/应用；表格候选；图表候选；下一步：extract_table_structure
- Page 12: medium；关键词命中：设计/制造；表格候选；图表候选；下一步：extract_table_structure
- Page 19: medium；关键词命中：设计；表格候选；图表候选；下一步：extract_table_structure
- Page 20: medium；关键词命中：制造；表格候选；图表候选；下一步：extract_table_structure
- Page 21: medium；关键词命中：设计；表格候选；图表候选；下一步：extract_table_structure

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
