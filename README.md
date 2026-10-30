# Agentic_RAG

## 项目目的

Agentic_RAG 是面向企业文档与项目台账的知识问答系统。通过检索增强生成（RAG）和有状态 Agent，将分散在 PDF、Word 文档及结构化台账中的信息转化为可检索、可追溯的答案，支持技术资料查询、文档摘要和项目数据筛选。

## 功能概览

- **文档处理与知识入库**：解析 PDF 和 DOCX，提取正文、表格、页码与章节信息，构建版本化知识索引。
- **混合检索与问答**：结合 BGE 向量检索、BM25、RRF 融合、重排序和元数据过滤，提供带来源引用的回答及无依据拒答。
- **LangGraph Agent**：通过知识搜索、全文摘要和项目台账查询工具处理任务，支持 SQLite 会话持久化与多轮追问。
- **交互工作台**：提供知识问答、Agent 对话、检索证据、文档列表、项目台账及评估结果浏览。
- **数据与评估工具**：包含公开数据准备脚本、QA 与 Agent 任务格式、RAGAS 评估、检索消融、参数搜索及留出集管理。

主要技术：Python、LangChain、LangGraph、Chroma、BGE、BM25、DeepSeek、Qwen、MLX、RAGAS。

## 简单 Demo

安装 Python 3.11 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)，执行：

```bash
git clone https://github.com/wuzhao321/Agentic_RAG.git
cd Agentic_RAG
uv sync --python 3.11 --locked
uv run --no-sync kb demo
```

首次安装依赖需要联网；演示运行无需 API 密钥、模型权重或业务文档。
程序在临时目录生成 Word 接口说明，解析并检索 `timeout_ms`，再查询合成项目台账。
输出 JSON 中可看到：

- `chunks` 为 `2`，包含正文与表格片段。
- `hits` 的表格证据包含 `timeout_ms | integer | 3000 毫秒`。
- `ledger` 返回“演示项目”，金额为 `100000` 元。

该演示覆盖文档解析、BM25 检索和台账筛选；完整的 BGE 检索、生成式问答与 Agent 网页体验见
[网页 Demo 指南](docs/demo.md)。

## 项目结构与导航

```text
Agentic_RAG/
├── src/kb/
│   ├── ingest/           # PDF/DOCX 解析、分块、元数据与索引
│   ├── retrieval/        # 向量/BM25 检索、融合、重排与路由
│   ├── chains/           # RAG 问答链与引用处理
│   ├── agent/            # Agent 状态图、会话与工具
│   ├── eval/             # 评估、消融、审核与报告生成
│   ├── web.py            # 网页服务与接口
│   ├── web_assets/       # 工作台前端
│   ├── local_demo.py     # 本地模型服务启动管理
│   ├── embedding_server.py # Dify 使用的本机 BGE 向量接口
│   └── cli.py            # 命令行入口
├── configs/              # 入库、检索、Agent、评估与演示配置
├── scripts/              # 数据准备、模型下载与端到端检查
├── tests/                # 自动化测试
├── docs/                 # 技术设计、使用指南与数据说明
├── data/                 # 语料、台账、索引与运行产物目录
├── models/               # 本地模型目录
├── .github/workflows/    # 持续集成
├── pyproject.toml        # 依赖、命令与工具配置
└── uv.lock               # 依赖版本锁定
```

| 想查看的内容 | 入口 |
| --- | --- |
| 系统架构与数据流 | [技术设计](docs/design.md) |
| 安装、文档接入与命令行使用 | [使用指南](docs/usage.md) |
| 网页工作台与本地模型启动 | [演示指南](docs/demo.md) |
| QA、Agent 任务与评估命令 | [评估协议](docs/evaluation.md) |
| Docker、Dify 知识库与对照评估 | [Dify 操作说明](docs/dify.md) |
| LangSmith 云端调用追踪 | [追踪操作说明](docs/langsmith.md) |
| 检索方案与消融方法 | [检索消融](docs/ablation.md) |
| 公开数据来源、转换与字段映射 | [数据说明](docs/public-data/README.md) |
| Agent 工具实现 | [知识搜索](src/kb/agent/tools/knowledge_search.py) · [文档摘要](src/kb/agent/tools/document_summary.py) · [台账查询](src/kb/agent/tools/project_query.py) |

模型权重、原始语料、索引、会话和运行结果在本地生成，通过 `.gitignore` 管理。
