# 网页工作台与本地模型

## 运行方式

网页工作台支持 DeepSeek 云端模式和本地 Qwen 模式。已配置 DeepSeek 的项目使用
`uv run --no-sync kb demo-web --cloud` 启动，BGE embedding 和 reranker 仍在本机 CPU 上运行。
本地模式使用 MLX 运行 Qwen3-4B-Instruct-2507 的 4-bit 权重，适用于 Apple Silicon。
网页默认监听 `127.0.0.1:8765`，本地生成模型服务监听 `127.0.0.1:8081`；
模型版本和端口见 `configs/demo.yaml`。

知识问答和 Agent 均支持同一会话的多轮追问，分别保存上下文，互不混用。会话保存在 `data/sessions/demo.sqlite`。
知识问答先结合历史改写检索问题，再以独立问题和证据生成答案；历史超长时沿用 Agent 的摘要策略。
请求串行处理，并发请求返回忙状态。服务仅提供本地访问，不包含登录或多租户权限控制。

## 快速启动：公开数据与 DeepSeek

安装 Python 3.11 和 uv，以下命令均在项目根目录执行。生成模型使用 DeepSeek，
BGE 向量模型与重排模型在本机 CPU 上运行；首次安装与下载需要联网。

```bash
uv sync --python 3.11 --locked --extra models --extra eval
test -e .env || cp .env.example .env
```

在 `.env` 填入自己的配置：

```dotenv
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=替换为你的实际密钥
DEEPSEEK_MODEL=deepseek-flash
LANGSMITH_TRACING=false
```

下载固定版本的检索模型，准备公开文档与采购台账，构建索引并启动服务：

```bash
uv run --no-sync python scripts/download_models.py
uv run --no-sync python scripts/prepare_public_demo.py
uv run --no-sync kb ingest
uv run --no-sync python scripts/prepare_demo_eval.py
uv run --no-sync kb demo-web --cloud
```

打开 <http://127.0.0.1:8765>，在“知识问答”中输入 `FastAPI 中如何把查询参数设置为必选？`，
查看回答和来源；在 Agent 中输入 `查询2026年签订且合同金额大于200万元的项目，返回项目名称和金额。`。
回答由实际模型生成，以界面返回的证据为准。按 Ctrl+C 停止，后续可直接运行最后一条命令。
云端模式会将问题和相关证据发送到配置的 DeepSeek 接口；会话库与本地模式分开保存。
启动时检查索引与台账属于公开来源清单，切换模式前先停止旧服务。

## Apple Silicon 本地模式安装与启动

安装 Python 3.11 和 uv，在项目根目录执行：

```bash
uv sync --locked --extra models --extra eval --extra local
uv run --no-sync python scripts/download_models.py
uv run --no-sync python scripts/download_demo_model.py
uv run --no-sync python scripts/prepare_public_demo.py
uv run --no-sync kb ingest
uv run --no-sync python scripts/prepare_demo_eval.py
uv run --no-sync kb demo-web
```

打开 <http://127.0.0.1:8765>，按 Ctrl+C 关闭网页和模型服务。
模型服务日志位于 `data/demo/model-server.log`。已完成安装和入库后，可直接用
`uv run --no-sync kb demo-web` 启动。

数据准备脚本从固定 commit 下载 FastAPI 文档与示例代码，采购数据使用仓库保存的公告摘录。
重新准备资料后应重新入库、生成演示评估集并重启服务。数据来源见 [公开数据说明](public-data/README.md)。
其他平台可通过 [命令行工具](usage.md) 连接云端模型。

## 功能与示例

| 功能 | 操作示例 |
| --- | --- |
| 知识问答 | `FastAPI 中如何把查询参数设置为必选？` |
| 项目筛选 | `查询2026年签订且合同金额大于200万元的项目，返回项目名称和金额。` |
| 多轮追问 | 在台账查询后输入 `它的合同签订日期是什么时候？` |
| 文档摘要 | `总结 public_demo/浙江税务GPU算力采购公告.pdf 的主要信息。` |
| 无依据拒答 | `公司报销出租车费用的上限是多少？` |
| 检索证据 | 切换“检索证据”，查看原文、来源与重排分数 |

“文档与台账”提供文档来源链接和摘要入口；“评估与来源”展示证据定位与批量检查结果。
输入框 Enter 发送，Shift+Enter 换行。新建会话分配独立编号，历史会话仍保存在 SQLite。

## 检索与工具回答

`prefer_exact_section` 在问题完整包含不少于四字符的子章节名时，保留一条对应的 BM25 证据。
文档总标题不参与优先规则；候选仍须通过重排相关性阈值，并遵守显式过滤条件。
该选项由 `configs/demo.yaml` 启用，独立于 A–D 实验的默认配置。

网页 Agent 的单次成功工具调用完成后，会进入 `respond` 节点：知识搜索复用 RAG 引用链，
台账直接渲染工具字段，文档摘要沿用工具结果。最终答案替换会话历史中的生成草稿。
多工具任务使用 Agent 的组合推理流程。引用校验负责引用格式和范围，语义质量由评估流程检查。

## 批量检查与文件

服务启动后运行：

```bash
uv run --no-sync python scripts/check_demo.py
```

检查脚本按 `data/demo/qa.jsonl` 和 `agent_tasks.jsonl` 执行请求，并保存同一会话的连续追问。
每次运行创建 `data/demo/runs/<UTC时间>/`，最近目录记录在 `data/demo/latest-run.txt`。

| 文件 | 内容 |
| --- | --- |
| `data/demo/manifest.json` | 来源、固定版本、原始与转换文件哈希 |
| `data/registry/documents.csv` | 文档登记、类型、项目与解析状态 |
| `data/ledger/projects.json` | 结构化采购项目台账 |
| `data/demo/qa.jsonl` | 带参考答案与证据的演示问答 |
| `data/demo/agent_tasks.jsonl` | 工具任务及期望参数 |
| `data/demo/review.json` | 原始来源、解析结果与索引的证据定位 |
| `data/demo/runs/.../summary.json` | 请求结果与自动检查汇总 |

答案要点、参考片段命中、工具选择和严格参数匹配分别统计；字符串匹配与证据命中不等同于
RAGAS 忠实度或人工任务完成率。详细指标和数据划分方式见 [评估协议](evaluation.md)。

## 扩展公开语料与审核

```bash
uv run --no-sync python scripts/prepare_extended_public.py
uv run --no-sync python scripts/prepare_review_tasks.py
uv run --no-sync kb ingest
```

扩展脚本下载 FastAPI 教程、读取采购公告摘录，并从 Kubernetes OpenAPI 规范提取对象定义。
转换件保留原始来源、版本、许可证和处理说明；同一规范中的对象定义共用其上游来源。
`data/review/public-v1/review.html` 提供 200 条候选 QA 的逐条审核与导出；20 个表格解析对照、40+20 Agent 候选任务同目录保存。
网页服务运行时可直接打开 `http://127.0.0.1:8765/review`。审核者逐条检查来源后填写本人姓名，
批准或驳回并导出审核 JSON；使用 `scripts/import_qa_reviews.py` 导入，全部有效批准后才能进入正式划分。
候选生成、审核导入与正式数据划分分别执行，详见 [评估协议](evaluation.md)。

## 离线最小示例

使用 Python 3.11 和 uv，在项目根目录执行：

```bash
uv sync --python 3.11 --locked
uv run --no-sync kb demo
```

首次安装依赖需要联网；运行无需 API 密钥、模型权重或业务文档。
程序在临时目录生成 Word 接口说明，解析并检索 `timeout_ms`，再查询合成项目台账。
返回的 JSON 包含 2 个知识片段、带 `3000 毫秒` 的表格证据，以及金额为 `100000` 元的演示项目。
该示例验证解析、BM25 检索和台账筛选；完整模型问答请使用本页的网页启动流程。

README 中的截图和 MP4 为公开工作台的真实运行展示，视频添加了说明字幕；其展示素材位于
`docs/assets/`，作为文档资源纳入版本管理。录制中间帧、制作文件和本地运行记录保存在 `.local/`。
