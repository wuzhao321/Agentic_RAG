# 使用指南

## 安装依赖

使用 Python 3.11 和 uv，在项目根目录执行：

```bash
uv sync --locked --extra models --extra eval
```

依赖按用途组织：基础依赖提供 CLI、文档解析、索引与 Agent；`models` 包含本地 BGE 推理依赖；
`eval` 提供 RAGAS 与数据集工具；`local` 提供 Apple Silicon 上的 MLX 本地生成服务。
依赖版本由 `uv.lock` 固定。

## 模型与配置

```bash
uv run --no-sync python scripts/download_models.py
```

BGE embedding 和 reranker 下载到 `models/`，版本在配置中固定。
Linux/Windows 的 PyTorch 使用 CPU 包源，macOS 使用标准 PyTorch 包；检索推理配置为 CPU。
网页工作台的本地 Qwen 安装与启动见 [演示指南](demo.md)。

| 配置文件 | 用途 |
| --- | --- |
| `configs/ingest.yaml` | 文档路径、分块、模型与 HNSW 参数 |
| `configs/retrieval.yaml` | A–D 检索变体、候选数量、阈值与路由 |
| `configs/agent.yaml` | 工具推理、会话摘要与模型参数 |
| `configs/eval.yaml` | 数据划分、评估路径与指标配置 |
| `configs/demo.yaml` | 网页端口、本地模型与演示检索覆盖项 |

## 接入 PDF / DOCX

1. 将文档放入 `data/raw/`，可按项目创建子目录。
2. 执行 `uv run --no-sync kb scan`，生成文档登记表。
3. 在 `data/registry/documents.csv` 填写 `doc_type`、`project_name`，按需补充 `project_id`。
   `doc_type` 接受技术规范、交付报告、接口说明书、其他；再次扫描会保留已填写的元数据。
4. 执行 `uv run --no-sync kb ingest`，生成版本化索引及 `docs/reports/ingest-report.json`。
5. 执行 `uv run --no-sync kb search 'CreateOrder 请求参数'` 查看检索证据。

解析器读取 PDF 文本层，不执行 OCR。PDF 来源保留页码，DOCX 来源使用章节路径。
表格按行分块并重复表头；超长单行产生告警，需要结合模型输入长度检查。
相同内容与配置重复入库会复用已有索引版本，修改文档后构建新版本并切换活动索引。

## 知识问答与 Agent

复制 `.env.example` 为 `.env`，设置 `DASHSCOPE_API_KEY` 和具体的 `QWEN_MODEL` 版本。
凭据文件由 Git 忽略。云端命令将问题和相关证据发送到配置的模型接口。

```bash
uv run --no-sync kb ask 'CreateOrder 的 timeout_ms 是多少？'
uv run --no-sync kb chat --session project-demo
```

`ask` 使用 LCEL 问答链；`chat` 使用 LangGraph 工具调用和 SQLite 会话。
输入 `/exit` 结束对话，不同 `--session` 使用独立历史；历史达到阈值后摘要旧轮次并保留近期消息。
LangSmith 追踪由 `.env` 控制，默认关闭。

## 项目台账

台账位于 `data/ledger/projects.json`。以下为字段示例：

```json
[
  {
    "project_id": "DEMO-001",
    "project_name": "示例项目",
    "signed_date": "2026-01-01",
    "amount": "100000.00",
    "owner": "示例负责人"
  }
]
```

金额单位为人民币元，使用字符串保留小数精度；日期格式为 `YYYY-MM-DD`。
可选字段包括采购人、供应商、原始金额和来源 URL，定义见
[`project_query.py`](../src/kb/agent/tools/project_query.py)。筛选由代码执行，支持日期范围、金额范围与字段投影。

## 检查与评估

```bash
uv run --no-sync kb doctor
uv run --no-sync kb demo
uv run --no-sync pytest -q
uv run --no-sync ruff check src scripts tests
uv run --no-sync ruff format --check src scripts tests
```

`doctor` 检查配置与数据路径；`demo` 在临时目录生成样例，检查解析、检索和台账查询。
批量模型评估、数据格式和审核导入见 [评估协议](evaluation.md)，检索实验见 [检索消融](ablation.md)。
