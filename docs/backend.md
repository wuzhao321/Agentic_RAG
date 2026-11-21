# 文档管理与异步入库后端

FastAPI 提供文档、任务和知识库查询接口。PostgreSQL 保存文档目录、任务状态和幂等记录。独立 Worker 从数据库领取任务，复用文档解析和索引构建逻辑。服务面向单实例、单知识库部署。

## 启动与配置

在项目根目录安装依赖并启动本地数据库：

```bash
uv sync --locked --extra models
uv pip install -r requirements-backend.txt
docker compose -f compose.backend.yaml up -d --wait
```

根据 `.env.example` 设置 `KB_DATABASE_URL`，并按 [使用指南](usage.md#模型与配置) 准备检索模型。也可使用已运行的 PostgreSQL。后端附加依赖由 `requirements-backend.txt` 声明，后续命令使用 `--no-sync`。

```bash
uv run --no-sync kb backend-init
uv run --no-sync kb api      # 终端一
uv run --no-sync kb worker   # 终端二
```

`backend-init` 执行迁移，将登记表中的原文复制到受管目录并保留文档 ID 与项目字段。再次导入时跳过已有记录。空知识库可使用 `backend-init --skip-import`，Worker 可通过 `--once` 领取至多一个任务。

| 配置 | 用途 |
| --- | --- |
| `configs/backend.yaml` | API 端口、上传大小和 Worker 轮询间隔 |
| `configs/ingest.yaml` | 原文、索引目录、分块和检索模型 |
| `KB_DATABASE_URL` | PostgreSQL 连接串，使用 `postgresql+psycopg://` |
| `KB_HOST`、`KB_ALLOWED_HOSTS` | 绑定地址与允许的 Host |
| `KB_ACCESS_TOKEN` | 服务访问凭据，绑定非回环地址时至少 32 字符 |

## HTTP 接口

默认接口文档为 `http://127.0.0.1:8780/docs`。配置访问凭据后，请求须携带 Bearer token。

| 方法与路径 | 行为 |
| --- | --- |
| `POST /documents` | 以 multipart 上传 PDF 或 DOCX，提供 `doc_type`、`project_name`，可选 `project_id` |
| `GET /documents` | 用 `offset` 和 `limit` 分页，可按项目名、文档类型筛选 |
| `POST /ingest-jobs` | 提交全量索引任务，提供 `Idempotency-Key` 和 JSON 对象（可为 `{}`） |
| `GET /ingest-jobs`、`GET /ingest-jobs/{id}` | 查看任务列表、状态、阶段和结果 |
| `POST /ingest-jobs/{id}/retry` | 将失败或中断任务重新排队 |
| `POST /api/query` | 传入 `question`、`session` 和 `mode`，模式可选 `search`、`rag`、`agent` |
| `GET /api/status`、`GET /api/review` | 工作台状态和审核页面数据 |
| `GET /health` | 检查数据库连通性 |

上传后提交索引任务，轮询任务 ID，待状态为 `succeeded` 后用 `search` 检查返回的引用片段。上传、入库和 `search` 使用解析与检索组件。`rag` 和 `agent` 另外使用配置的生成模型。

## 任务状态与索引发布

- 任务状态包括 `pending`、`running`、`succeeded`、`failed` 和 `interrupted`。进度计数对应当前阶段。
- 相同幂等键和请求返回已有任务。相同键配不同请求返回 409。文档目录变化后，应为新的重建提交新键。
- Worker 在短事务中领取任务和更新进度，在事务外执行模型推理。CLI 与 Worker 共享索引写锁。
- 发布前检查任务创建时的文档清单。验证通过后原子切换 `active.json`，查询服务自动加载新索引。构建失败时，原索引继续提供检索。
- Worker 重启时，根据活动索引的任务标识补记已发布任务；其余运行中任务标记为中断，可手动重试。
- 后端从 PostgreSQL 读取文档清单。原有 `kb scan` 跳过受管上传目录。`kb ingest` 按 CSV 构建索引，使用后端管理知识库时应通过任务接口更新。

## 测试

```bash
uv run --no-sync pytest -q -rs tests/test_backend.py tests/test_ingest.py tests/test_retrieval.py
uv run --no-sync ruff check src scripts tests
uv run --no-sync ruff format --check src scripts tests
```

后端测试使用临时文件和轻量模型替身。设置 `KB_TEST_DATABASE_URL` 可在 PostgreSQL 临时 schema 上测试；另设 `KB_TEST_REAL_BGE=1`，并提前准备本地 BGE 模型，可运行 HTTP 与独立 Worker 集成用例 `tests/test_backend.py::test_five_http_endpoints_and_upload_validation`。部署方式见 [Sites 部署](sites-deployment.md)。
