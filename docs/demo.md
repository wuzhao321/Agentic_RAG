# 网页工作台与本地模型

## 运行方式

网页工作台使用 MLX 运行 Qwen3-4B-Instruct-2507 的 4-bit 权重，适用于 Apple Silicon。
BGE embedding 和 reranker 在 CPU 上运行。网页默认监听 `127.0.0.1:8765`，模型服务监听
`127.0.0.1:8081`；模型版本和端口见 `configs/demo.yaml`。

知识问答使用单轮上下文，Agent 支持同一会话的多轮追问，会话保存在 `data/sessions/demo.sqlite`。
请求串行处理，并发请求返回忙状态。服务仅提供本地访问，不包含登录或多租户权限控制。

## 安装与启动

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

![网页工作台](images/workspace.png)

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
