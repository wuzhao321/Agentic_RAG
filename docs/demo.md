# 本地 Demo 操作手册

## 当前演示环境

本机 Apple Silicon / 16 GiB 内存，Python 3.11。网页仅监听 `127.0.0.1:8765`，
MLX 模型服务监听 `127.0.0.1:8081`。Qwen3-4B-Instruct-2507 使用 4-bit 权重；
BGE-large-zh-v1.5 和 BGE-reranker-base 使用 CPU。模型共约 4.4 GB。

演示不需要云 API 密钥。首次问答会预热缓存；并发请求会收到“模型忙”提示，
本机 demo 按单请求运行。网页知识问答是单轮，Agent 模式支持同一会话内的多轮追问。
新建会话分配新编号，旧会话仍保存在 `data/sessions/demo.sqlite`。

网页 demo 对完成单次工具调用的回答增加证据处理：知识检索结果使用 RAG 引用校验链作答；
台账按工具返回字段直接展示，保留金额精度与单位；全文摘要直接展示摘要工具结果。
最终答案替换会话历史中的生成草稿。多个工具的组合任务继续使用原生 Agent 推理流程，
该类任务仍需要逐条核对最终回答，引用格式校验不能证明语义完全忠实。

`configs/demo.yaml` 启用了 `prefer_exact_section`：当问题完整包含不少于四字符的子章节名时，
保留一条对应的 BM25 召回证据；它仍须通过重排相关性阈值，并保留真实重排分数。
文档总标题不参与优先排序。这用于减少“必选/可选”等相似章节混淆，不更改正式 A–D 实验的默认配置。

## 启动

在项目根目录执行：

```bash
.venv/bin/kb demo-web
```

打开 <http://127.0.0.1:8765>。按 Ctrl+C 关闭本次启动的两个服务。
如关闭终端后页面无法访问，重新执行启动命令即可。日志在 `data/demo/model-server.log`。
不得将这个无登录的本机 demo 直接部署为公网服务。

## 五分钟演示路线

1. 打开“文档与台账”，展示 5 份 FastAPI 官方文档、2 份官方采购公告摘录、原始来源链接。
2. 回到“知识助手”，选择“知识问答”，输入：
   `FastAPI 中如何把查询参数设置为必选？`
   查看回答中的 [S1] 引用，再在右侧比对原文。
3. 选择“Agent 助手”，输入：
   `查询2026年签订且合同金额大于200万元的项目，返回项目名称和金额。`
   右侧展开 ProjectQueryTool，检查日期边界、万元到人民币元的换算、返回的官方来源。
4. 继续输入：`它的合同签订日期是什么时候？`，检查多轮上下文是否正确沿用项目名。
5. 新建会话，选择“Agent 助手”，输入：
   `总结 public_demo/浙江税务GPU算力采购公告.pdf 的主要信息。`
   检查 DocumentSummaryTool 的真实调用及 PDF 页码。
6. 选择“知识问答”，输入：`公司报销出租车费用的上限是多少？`
   当前语料没有该信息，应拒答。

“文档与台账”里的“用 Agent 总结这份文档”会自动填写精确文档名。
输入框 Enter 发送，Shift+Enter 换行。回答保留模型原始文本，证据以纯文本安全显示。

## 从空环境复现（Apple Silicon）

先安装 Python 3.11 和 uv，然后在项目根目录执行：

```bash
uv sync --locked --extra models --extra eval --extra local
uv run --no-sync python scripts/download_models.py
uv run --no-sync python scripts/download_demo_model.py
uv run --no-sync python scripts/prepare_public_demo.py
uv run --no-sync kb ingest
uv run --no-sync python scripts/prepare_demo_eval.py
uv run --no-sync kb demo-web
```

本机也可使用 `.tools/bin/uv`，并设置 `UV_CACHE_DIR="$PWD/.cache/uv"`
和 `UV_PYTHON_INSTALL_DIR="$PWD/.tools/python"`。所有模型版本已固定。
公开采购公告直接连接可能失败，数据准备脚本使用随项目保存的已核对公告快照，
不会在下载失败时制造业务事实。FastAPI 文档与引用代码从固定 commit 下载。

重新准备公开数据会更新这批演示文档与对应的两个台账条目，保留其他台账和元数据。
完成重新入库后，应重新运行评估集准备脚本并重启网页，以加载同一版索引。

Linux/Windows 可以使用已有云端 `ask` / `chat` 路径；此 MLX 网页启动器仅在
Apple Silicon 验证，未宣称跨平台本地推理可用。

## 验证与文件

服务启动后运行：

```bash
.venv/bin/python scripts/check_demo.py
.venv/bin/pytest -q
.venv/bin/ruff check src scripts tests
```

演示端到端检查保存 20 次 RAG、8 次 Agent 和 2 轮连续对话的实际请求与输出。
每次运行创建独立目录 `data/demo/runs/<UTC时间>/`，最近一次目录保存在
`data/demo/latest-run.txt`。错误也会保留，不会被计作通过。

| 文件 | 内容 |
| --- | --- |
| `data/demo/manifest.json` | 来源、固定版本、原始与转换文件 SHA-256 |
| `data/registry/documents.csv` | 文档登记表、类型、项目、解析状态 |
| `data/ledger/projects.json` | 可精确筛选的采购项目台账 |
| `data/demo/qa.jsonl` | 18 道有依据 QA、2 道拒答题 |
| `data/demo/agent_tasks.jsonl` | 三类工具的 8 项任务及期望参数 |
| `data/demo/review.json` | 原始来源→转换文档→索引片段的证据核对 |
| `data/demo/runs/.../summary.json` | 真实输出的自动检查汇总 |

QA 答案要点检查、参考片段命中、工具选择和严格参数匹配均是开发指标，
不等同于 RAGAS 忠实度或人工任务完成率。query 同义改写可能使严格参数匹配失败，
需要结合完整工具轨迹判断。演示数据全部标记非正式验收，未创建或读取正式留出集。

当前仍待正式项目阶段完成：业务方审核的验收数据、目标规模语料、RAGAS 消融实测、
鉴权与权限隔离、上传管理和部署运维。已有 CLI 可接入自有 PDF/DOCX 并重新入库。
