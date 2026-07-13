# 项目目录说明

## 核心输入

- `data/raw_pdf/`：原始 PDF 存放目录。日常运行时可以只写 PDF 文件名，脚本会到这里查找。
- `data/rendered_pages/`：PDF 页面渲染图，供表格、图示、OCR 或证据定位使用。
- `data/samples/`：样例数据。

## 文档元数据与知识库台账

- `metadata/documents.csv`：PDF 审计后的文档清单，按 PDF 内容哈希记录 `doc_id`、文件路径、页数等。
- `metadata/page_manifest.csv`：页面级清单，记录每页的解析优先级、质量和复核信息。
- `metadata/kb_registry.json`：root-model 知识库台账。一个 root-model 对应一个隔离的 Qdrant collection，可登记多个 PDF；同一 PDF 只解析一次。
- `metadata/*.bak_*.csv`：历史备份，可用于回滚元数据。

## 解析与证据库

- `parsed_documents/`：Stage2 生成的结构化证据库，每个 `doc_id` 一个目录，包含 `evidence_chunks.jsonl`、`document_structure.json`、页面记录、表格和图示记录。
- `parsed_documents/latest_run.txt`：最近一次 Stage2 解析目录指针。多 PDF/root-model 流程不再依赖它决定知识库范围，只作为兼容指针保留。
- `outputs/stage1_document_audit/`：Stage1 文档审计输出。
- `outputs/stage2_evidence_build/`：Stage2 证据库构建报告和人工抽检材料。

## RAG 与建模中间产物

- `rag/queries/`：RAG 检索和抽取任务模板。
- `rag/config/`：各阶段配置。
- `rag/config/document_profiles/`：按具体文档隔离的解析规则。`generic.yaml` 不含固定行业判断；特定报告的固定页码、页眉、标题和元数据只能放在独立档案中。
- `rag/templates/`：产业链树模板；默认使用 `document_driven`。
- `rag/outputs/`：Stage3A/3C/4/4.5/4.6/最终树等运行输出。`rag/latest_*_run.txt` 指向当前有效链路。
- `rag/indexes/`：Qdrant 索引构建摘要和 manifest。

## 向量知识库

- `qdrant_storage/`：本地 Qdrant 持久化数据。不同 root-model 使用不同 collection，避免不同行业串库。

## 最终交付与日志

- `final_deliverables/`：最终交付目录，包含树节点/边 CSV、JSON、PNG/SVG/HTML、流程报告。
- `logs/`：各阶段运行日志。
- `archive/`：清理或归档脚本移动出的旧产物。

## 脚本

- `scripts/run_industry_chain_one_click.py`：主入口，支持一个 root-model 下输入多个 PDF。
- `run_chain.ps1`：PowerShell 简化入口。
- `scripts/stage*.py`：各阶段脚本。

## 推荐知识库规则

- 用 `root-model` 表示一个产业链知识库，例如 `半导体产业链`、`新型显示产业链`。
- 同一 root-model 可以登记多个 PDF，形成一个合并模型。
- 不同 root-model 使用不同 Qdrant collection，互不影响。
- 同一 PDF 通过 SHA256 去重；已解析过的 PDF 会复用 `parsed_documents/`，不会重复 Stage1/Stage2。

## 专用规则隔离

- 一键通用流程默认使用 `--document-profile auto`，逐 PDF/doc_id 匹配；未命中特定报告时使用 `generic`。
- `china_semiconductor_whitepaper` 仅匹配《中国半导体白皮书》，保留该报告的固定页码和页眉规则，不会由宽泛的“半导体”行业词触发。
- 半导体固定树保留在 `rag/templates/semiconductor.yaml`，只有显式选择该模板时生效。
- 旧版 Stage5/5.5/6 网络图脚本包含半导体专用结构，已从一键层级树流程中隔离，并要求显式传入 `--specialization semiconductor` 才能运行。
