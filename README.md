# 企业知识库 RAG 与 LangGraph Agent

依据项目计划书及配套执行文档搭建，现已提供可运行的本地网页 demo。
使用真实 BGE 检索、Qwen3 4B 本地生成和 LangGraph 原生工具调用，无需云端密钥。
已导入 7 份公开文档、2 条公开采购项目台账，准备 20 道来源已核对的演示 QA 和 8 项 Agent 任务。
**演示数据经过助手核对，未经过业务人员人工审核；不代表正式业务验收结果。**

## 网页 demo

本机已安装依赖、下载模型并建立索引。在项目根目录运行：

```bash
.venv/bin/kb demo-web
```

打开 **http://127.0.0.1:8765**。终端按 `Ctrl+C` 同时关闭网页和模型服务。
如果提示端口被占用，先检查本项目是否已经启动；不要重复启动。

- 知识问答：混合检索、重排、回答引用、无依据拒答。
- Agent 助手：知识检索、全文摘要、项目台账查询；支持多轮追问和 SQLite 会话。
- 检索证据：查看原文片段、来源、重排分数，不调用生成模型。
- 文档与台账：官方来源链接、转换说明、人民币元金额。
- 评估与来源：来源核对口径及数据文件位置。

详细步骤与演示问题见 [Demo 操作手册](docs/demo.md)，公开来源见
[数据说明](docs/public-data/README.md)，实际运行结果见 [Demo 实测报告](docs/reports/demo-validation.md)。正式验收仍使用独立的评估协议。

## 立即运行

以下为不启动网页的工程检查命令；旧 `demo` 命令仍保留为快速合成样本冒烟测试。

在本项目根目录执行（本机已经创建 `.venv`）：

```bash
.venv/bin/kb demo
.venv/bin/kb doctor
.venv/bin/pytest -q
.venv/bin/ruff check src scripts tests
```

`demo` 在临时目录生成合成 DOCX，验证表格解析、BM25 精确字段检索和项目台账筛选。
它不调用云 API，也不使用假模型冒充 BGE。测试中的模型替身仅用于验证控制流。

## 新机器安装

需要 Python 3.11 和 uv。本机 uv 位于 `.tools/bin/uv`；下面也可将 `uv` 替换为该路径。

```bash
uv sync --locked
uv sync --locked --extra models --extra eval
uv run --no-sync python scripts/download_models.py
uv run --no-sync python scripts/smoke_models.py
uv run --no-sync python scripts/smoke_retrieval.py
```

本机在受限写入环境下运行 uv 时使用：

```bash
UV_CACHE_DIR="$PWD/.cache/uv" UV_PYTHON_INSTALL_DIR="$PWD/.tools/python" .tools/bin/uv sync --locked --extra models --extra eval
```

默认依赖用于离线测试；`models` 增加 sentence-transformers / PyTorch。
`eval` 增加 RAGAS 0.3.9，并固定与其兼容的 langchain-community 0.3.31。
macOS 使用标准 PyTorch 包，推理明确指定 `device: cpu`。Linux/Windows 已在 pyproject 中
指定 CPU PyTorch 源；跨平台安装仍需在相应系统验证，详见 [环境记录](docs/setup-log.md)。
模型下载脚本将仓库 commit SHA 写入 YAML，后续下载复用同一 revision。
所有模型权重与运行数据均被 `.gitignore` 排除。

## 接入真实文档

1. 将经过确认的 PDF / DOCX 放入 `data/raw/`，允许按项目建子目录。
2. 执行 `.venv/bin/kb scan`。
3. 补齐 `data/registry/documents.csv` 的 `doc_type`、`project_name`，建议同时补齐 `project_id`。
   `doc_type` 只接受：技术规范 / 交付报告 / 接口说明书 / 其他。再次扫描保留人工填写字段。
4. 执行 `.venv/bin/kb ingest`，查看 `docs/reports/ingest-report.json`。
5. 执行 `.venv/bin/kb search 'CreateOrder 请求参数'`，本步骤仅本地检索。

扫描件会产生告警，不做 OCR。PDF 表格采用 PyMuPDF 的网格/布局识别，复杂表格仍需人工抽检。
DOCX 页码不可可靠恢复，来源使用章节路径。表格超过目标长度时按行分组、重复表头；单行超长
会保留完整行并告警。BGE 输入长度仍有限制，这类异常行需要人工拆分或后续专用处理。

入库采用版本化索引：先写完整 Chroma collection 和同批 chunk JSONL，再原子切换 `active.json`。
相同内容与配置重复入库直接复用；文档修改后切换新版本，当前查询不会混入旧 chunk。
旧版本保留以便故障恢复，尚未实现垃圾回收；重复修改大批文档会增加磁盘占用。
BM25 从同批 JSONL 建立，避免使用 pickle。当前按过滤子集计分，性能还需真实语料测试。

## 通义千问与 Agent

复制 `.env.example` 到 `.env`，仅在本地填写 `DASHSCOPE_API_KEY` 和具体版本的 `QWEN_MODEL`。
不要把密钥发到聊天或提交 Git。`QWEN_MODEL` 没有设置默认别名，避免模型版本静默漂移。

```bash
.venv/bin/kb ask 'CreateOrder 的 timeout_ms 是多少？'
.venv/bin/kb chat --session project-demo
```

`ask` 使用 LCEL RAG 链；`chat` 使用 LangGraph 原生 Function Calling 与 SQLite 持久化会话。
两个命令会把问题、相关证据或工具结果发送至配置的通义千问 API；只接入允许外发的资料。
历史超过阈值时摘要旧轮次，保留最近 4 轮，工具调用与工具结果不会被拆散。
输入 `/exit` 退出；不同 `--session` 相互隔离。

项目查询工具读取 `data/ledger/projects.json`。字段示例（以下均为合成数据）：

```json
[
  {
    "project_id": "DEMO-001",
    "project_name": "演示项目",
    "signed_date": "2026-10-23",
    "amount": "100000.00",
    "owner": "测试负责人"
  }
]
```

`amount` 单位为人民币元，建议使用字符串保留小数精度；日期为 `YYYY-MM-DD`。
该版台账采用明确的英文字段名，业务原表若使用中文字段，需先映射到此格式。
查询由代码过滤，不执行模型生成的 SQL。

LangSmith 默认关闭。在确认追踪数据可以外发后，填写其密钥并设置 `LANGSMITH_TRACING=true`。
缺少台账时知识检索与文档摘要仍可用，台账工具返回错误给 Agent。

## 参数与验证

- `configs/ingest.yaml`：分块、模型、路径与 HNSW。
- `configs/retrieval.yaml`：A 纯向量，B 加 BM25/RRF，C 加重排，D 加规则路由；默认 D。
- `configs/agent.yaml`：推理上限、摘要、LLM 与持久化。
- `configs/eval.yaml`：划分种子、数据路径与目标指标。

自动推断过滤条件无结果时回退；用户显式传入的过滤约束不会被丢弃。
向量距离与重排阈值只是初值，必须用调参集校准。引用校验能拒绝缺失/越界引用，
不能证明每句话与证据一致；Faithfulness 仍需要 RAGAS 和人工复核。

已人工审核的评估数据可执行：

```bash
.venv/bin/kb split-eval data/eval/qa_all.jsonl
```

数据字段见 [评估协议](docs/evaluation.md)。划分固定种子 42、按题型分层，结果禁止覆盖，
留出集设为只读。完成入库、人工审核标注和 Qwen 配置后运行：

```bash
# 只读取调参集，逐级运行 A/B/C/D
.venv/bin/kb eval-rag
# 调参集上运行 18 组 D 配置；输出建议，不自动改写配置
.venv/bin/kb eval-rag --grid
# 调参结束后冻结版本，再一次性评估留出集 A/D
.venv/bin/kb freeze-eval
.venv/bin/kb eval-rag --final
```

`eval-rag` 会调用通义千问，生成和裁判使用同一版本、temperature=0，评估 embedding 使用本地 BGE。
每次运行保存到 `data/eval/runs/`，包含配置、环境、数据哈希、逐条生成/评分、bootstrap 区间和
Markdown 对比表。无答案类单独统计拒答率；评分失败保留 null，并显示有效样本数。

最终锁在读取留出集前创建，失败也不自动删除；冻结后版本变化将拒绝最终运行。
请勿用留出集调试或把示例数据作为验收数据。详细约束见 [评估协议](docs/evaluation.md)。

Agent 使用首批 `agent_tasks_40.jsonl` 和新标注的 `agent_tasks_retest_20.jsonl`：

```bash
.venv/bin/kb eval-agent
.venv/bin/kb eval-agent --retest
.venv/bin/kb review-agent data/eval/runs/<运行目录> /路径/已填写审核.jsonl
```

每条任务使用独立会话，输出调用、参数、完整或部分轨迹、步数和延迟。
复制并填写运行目录的 `reviews.template.jsonl` 后导入，支持分批审核和版本保留。
未全部审核时全量任务完成率为 null，单独显示已审核子集的完成率。

## 当前阶段与后续工作

| 阶段 | 当前状态 |
| --- | --- |
| P0 | 环境、BGE 和本地 Qwen3 已验证；7 份公开文档与 2 条台账已接入，内部业务数据待接入 |
| P1 | Dify 操作清单与问题台账模板已准备，未部署、未验证 |
| P2 | 公开 PDF/DOCX 已形成 126 个真实索引片段；80 份文档及 20 个表格抽检未完成 |
| P3 | A–D 管线、消融与网格搜索 runner 已实现；业务调参尚未开始 |
| P4 | 本地 Qwen 知识问答、拒答和引用已实测；正式业务集与长对话效果待验证 |
| P5 | 三工具 Agent、网页与多轮追问已实测；批量评测和人工审核导入可用，正式任务验收待开展 |
| P6 | RAGAS、报告与留出集保护已实现；正式评估未运行 |

详见 [技术方案](docs/design.md)、[环境日志](docs/setup-log.md)、
[首轮报告](docs/reports/phase-0.md)、[评估链路报告](docs/reports/evaluation-foundation.md)
与 [消融报告](docs/ablation-report.md)。
