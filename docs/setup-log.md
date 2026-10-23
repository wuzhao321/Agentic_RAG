# 环境与安装记录

日期：2026-10-23（Asia/Shanghai）。项目根目录：`/Users/wuzhaowei/Documents/ChatGPT/RAG`。

## 初始检测

| 项目 | 结果 |
| --- | --- |
| 系统 / 架构 | Darwin / arm64 |
| CPU 逻辑核心 | 10 |
| 物理内存 | 17,179,869,184 bytes，16 GiB |
| 初始可用磁盘 | 约 884 GiB |
| 系统 Python | 3.9.6，不满足项目 3.11 要求 |
| uv / Docker / code | 初始 PATH 未发现 |
| Git | /usr/bin/git；空仓库、main、无初始提交 |
| Hugging Face | 放开命令网络访问后 HEAD 200；模型实际下载成功 |
| DashScope 域名 | HEAD 404，说明域名与 TLS 可达；不等于鉴权或模型调用通过 |

首次受限命令环境无法解析网络域名、无法读取 sysctl；获准执行环境检测后完成核对。
没有安装全局 Python、Homebrew、编辑器、扩展或 Docker。未启动 Dify。

## 安装动作

所有动作在检测后执行。仅向项目 `.tools`、`.venv`、`.cache`、`models` 写入。
下列命令中 uv 操作均使用 `UV_CACHE_DIR=$PWD/.cache/uv` 和
`UV_PYTHON_INSTALL_DIR=$PWD/.tools/python`。

| 命令 / 动作 | 结果 |
| --- | --- |
| `python3 -m pip install --target .tools uv` | uv 0.13.5，项目局部安装成功 |
| `.tools/bin/uv sync` | 下载 Python 3.11.17、创建 .venv、生成 uv.lock 并安装基础/开发依赖 |
| `.tools/bin/uv sync --extra models --locked` | 安装 sentence-transformers 与 macOS PyTorch，成功 |
| `.venv/bin/python scripts/download_models.py` | 两个公开 BGE 模型下载到 models，SHA 写回配置，成功 |
| `.tools/bin/uv lock` | 成功；锁定 164 个包，Linux/Windows 使用 CPU 源，移除 CUDA 依赖 |

macOS 使用 PyPI 的标准 PyTorch 包，运行时 `device: cpu`；Linux/Windows 通过
`tool.uv.sources` 指向 `https://download.pytorch.org/whl/cpu`。
依据 [uv 官方 PyTorch 集成文档](https://docs.astral.sh/uv/guides/integration/pytorch/)。
本机没有安装 CUDA 包；跨平台安装还需在对应操作系统验证。

## 已安装并核对的核心版本

| 包 | 版本 |
| --- | --- |
| langchain-core | 1.7.0 |
| langchain-openai | 1.7.2 |
| langchain-text-splitters | 1.1.3 |
| langgraph | 1.2.16 |
| langgraph-checkpoint-sqlite | 3.1.2 |
| chromadb | 1.5.9 |
| sentence-transformers | 5.7.0 |
| torch | 2.14.1 |
| transformers | 5.20.2 |
| python-docx | 1.2.0 |
| pymupdf | 1.28.2 |
| pytest / ruff | 9.1.1 / 0.17.2 |

已通过 `uv pip show` 与 Python introspection 核对 ToolNode、SqliteSaver、HNSW configuration、
近似 token 计数、SentenceTransformer/CrossEncoder 的已装接口。
首轮 RAGAS 仅列入可选依赖；第二轮已安装、核对接口并完成离线集成测试，见下文。

## 模型与 CPU 冒烟

| 模型 | 锁定 revision |
| --- | --- |
| BAAI/bge-large-zh-v1.5 | 79e7739b6ab944e86d6171e44d24c997fc1e0116 |
| BAAI/bge-reranker-base | 2cfc18c9415c912f9d8155881c133215df768a70 |

执行 `.venv/bin/python scripts/smoke_models.py`，开启 `HF_HUB_OFFLINE=1`：

- embedding：1024 维，向量范数 0.99999995；本次加载约 2.58 秒，单条编码约 2.37 秒。
- reranker：加载约 0.76 秒，两条 query-passage 打分约 1.03 秒；相关片段 0.9553，
  不相关片段被 0.5 初始阈值过滤。
- 这些是一次合成数据冒烟，**不是 P50/P95 或真实业务效果评测**。
- 权重约 2.3 GB；依赖与缓存另占用若干 GB。

执行 `.venv/bin/python scripts/smoke_retrieval.py`，同样离线：

- 合成 DOCX → 真实 BGE → Chroma + BM25 → RRF → 真实 reranker 全链路通过。
- 生成 3 个 chunk，重复入库复用相同版本。
- 正确返回 timeout_ms 为 3000 毫秒的表格及章节元数据。
- 无关问题“火星上有多少只企鹅？”返回 0 个片段。
- 数据与索引位于临时目录，结束后清理；没有用合成资料填充业务知识库。

通义千问冒烟未执行：没有提供 API Key / 具体模型版本。
本轮没有向 Qwen、LangSmith 或 Dify 发送文档内容。

## 自动化检查与已修复问题

- `pytest -q`：36 passed；5 条 PyMuPDF SWIG 类型弃用告警，不影响测试结果。
- `ruff check src scripts tests`：通过。
- `ruff format --check src scripts tests`：通过。
- `kb demo`：成功；`kb scan`：0 份真实文档，空登记表生成成功。
- `git check-ignore`：密钥、原始文档、登记表、台账、评测集、索引、模型及工具目录均被忽略。
- 新增 PDF 网格表格回归测试发现 `Table.extract()` 会把 timeout_ms 错排为 timeout ms _。
  已改为用表格单元格边界 + MuPDF 原始文本提取，回归测试通过。

## 未完成的准备项

真实 80 份文档盘点、内部台账字段映射、Qwen 冒烟、允许外发范围、Dify 原型、正式数据集标注。
没有读取或创建真实业务留出集；测试中的留出文件均为临时生成的测试夹具。

## 第二轮安装与验证（2026-10-23）

1. 先执行 `uv pip show ragas datasets`，确认尚未安装；随后在相同项目缓存和 Python 路径下执行
   `.tools/bin/uv sync --locked --extra models --extra eval`。
   安装 RAGAS 0.3.9、datasets 4.8.5 及评估依赖。
2. 首次真实导入发现 `langchain_community.chat_models.vertexai` 不存在。
   核对已装源码、PyPI 依赖元数据和 [RAGAS 官方仓库问题记录](https://github.com/vibrantlabsai/ragas/issues/2753)，
   在 pyproject 的 eval extra 中固定 langchain-community 0.3.31。
3. 执行 `.tools/bin/uv sync --extra models --extra eval`，更新 uv.lock 为 167 个包。
   RAGAS、LangChain wrapper、EvaluationDataset、三项 metric 和 RunConfig 的导入/签名核对通过。
4. 按 [RAGAS 0.3.9 evaluate 文档](https://docs.ragas.io/en/v0.3.9/references/evaluate/) 接入实际接口。
   集成测试使用真实 RAGAS 库与离线结构化模型替身，三项指标可执行，未调用任何云模型。

最终检查：

- `RAGAS_DO_NOT_TRACK=true .venv/bin/pytest -q`：**56 passed**，7 条已知依赖弃用告警。
- `ruff check src scripts tests` 与 `ruff format --check src scripts tests`：通过。
- `uv lock --check --offline`：通过；未重新下载模型。
- `kb doctor`：0 份业务文档，无台账、无业务索引、无评估集，两个本地模型存在且已固定 revision。
- `kb eval-rag --help`：命令可用；未配置 Qwen 时执行评估会简洁报错并停止，不读取留出内容或创建最终锁。
- CI 增加评估 extra 和显式 RAGAS 导入；本地验证通过，远端 CI 尚未运行。

RAGAS 的 LangChain wrapper 在 0.3.9 有弃用提示，但本次版本固定且集成测试通过。
后续升级应先验证导入与模型适配，不应仅依赖安装解析成功。

## 第三轮：公开数据与可运行 Demo（2026-10-23）

- 安装 `local` extra：mlx-lm 0.31.3、mlx 0.32.4；uv.lock 现解析 171 个包。
- 下载固定版本的 Qwen3-4B-Instruct-2507 MLX 4-bit 权重；本机实际生成和原生工具调用均成功。
- 下载 5 页 FastAPI 官方中文文档及其示例代码，保留 MIT 许可；从两个官方采购公告提取公开字段，
  保存来源核对快照。转换为 6 份 DOCX 和 1 份带文本层/表格的 PDF，共 126 个片段，无解析告警。
- 整理 2 条真实公开台账，金额用 Decimal 转换人民币元，负责人未公开字段不臆造。
- 准备 20 道助手来源核对 QA、8 项 Agent 任务；18 道有答案题的证据在原始来源、解析结果和
  索引中均定位通过。正式留出集未创建/读取。
- 新增本机网页、模型启动/退出管理、来源显示、工具轨迹、会话隔离、忙状态和错误反馈。
- 修复连续段落过度分块、代码缩进丢失、模型省略目录导致摘要找不到文件、严格金额边界问题。
- 自动化测试 **70 passed**；ruff 与离线 lock 检查通过。浏览器实际提交问题、显示回答和来源成功。

真实模型测试结果和保留差异见 [Demo 实测报告](reports/demo-validation.md)。
本轮不代表正式 RAGAS 验收，未配置云端密钥，未执行正式留出集或 Dify 评估。

## 第四轮：最终 Demo 验证（2026-10-24）

- 修复 Agent 单工具最终回答：台账精确渲染、知识问答引用链、摘要工具原样返回，并覆盖会话草稿。
- 真实复测定位“必选/可选”章节混淆；增加 demo 子章节精确匹配候选保留，排除总标题，保留阈值和过滤。
- 增加对应回归测试，最终 **79 passed**，7 条已知依赖弃用告警；ruff 检查、66 文件格式检查通过。
- 修复本机端口 TIME_WAIT 导致快速重启失败的问题；实际启动最终服务成功。
- 最终实测目录 `data/demo/runs/20261023T163945Z`：20 QA + 8 Agent 无执行错误；参考证据 18/18、拒答 2/2、工具 8/8；
  严格 QA 要点 19/20、Agent 要点 7/8、严格参数 6/8。保留数值格式、同义参数差异及失败历史。
- 两轮真实会话追问成功；最终 QA-004 与 AG-006 的必选声明及来源已复核。
- 新增 [Demo 实测报告](reports/demo-validation.md)，更新 README 和技术方案；正式验收边界保持明确。
- 浏览器实际提交金额/日期筛选任务成功，核对参数、返回项目、供应商及来源；截图保存为 `docs/reports/demo-final-browser.png`。
