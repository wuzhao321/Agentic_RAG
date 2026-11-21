# Sites 与独立后端部署

Sites Worker 提供网页和同源 HTTPS 代理。独立服务器运行 FastAPI、入库 Worker、BGE、Chroma、PostgreSQL 和反向代理。网页访问 Sites Worker，Worker 使用服务端凭据请求后端。

## 准备已有知识库

在首次迁移基于 CSV 的知识库时，在源项目运行：

```bash
uv run --no-sync python scripts/prepare_hosted_bundle.py .local/releases/rag-hosted
```

输出是同名目录和压缩包。导出内容包括部署源码、登记表引用的原文、词典、现有台账及用于工作台展示的本地评估资料。文件哈希记录在 `KNOWLEDGE_MANIFEST.json`。服务器从原文重建索引，并单独配置凭据与模型。

已有后端上传的文档由 PostgreSQL 目录管理。运行后的备份应同时覆盖 PostgreSQL 与持久数据目录。部署包可能包含原始业务文档及评估内容，应通过授权渠道传输。

## 启动服务器

安装 Docker Engine 与 Compose，配置域名解析并开放 80 和 443 端口。在项目或解压后的迁移目录运行：

```bash
cp deploy/hosted.env.example .env.hosted
# 编辑 .env.hosted 后运行：
docker compose --env-file .env.hosted -f compose.hosted.yaml up -d --build
docker compose --env-file .env.hosted -f compose.hosted.yaml logs -f initialize worker
```

| 变量 | 用途 |
| --- | --- |
| `BACKEND_DOMAIN` | HTTPS 后端域名，仅填写主机名 |
| `POSTGRES_PASSWORD` | 数据库密码，使用独立随机十六进制值 |
| `KB_ACCESS_TOKEN` | 后端 Bearer 凭据，至少 32 字符 |
| `LLM_PROVIDER` 及供应商模型变量 | 选择 DeepSeek 或 DashScope，变量见 `deploy/hosted.env.example` |

初始化下载固定版本的检索模型、迁移数据库并导入已有登记表。导入文档后提交初次入库任务。生成模型配置用于 RAG 和 Agent 问答。上传、入库与 `search` 模式可独立运行。

PostgreSQL 仅供 Compose 服务访问。数据、模型和 PostgreSQL 数据卷用于跨重启持久化。升级前备份数据并停止 `api` 和 `worker`，再执行构建和启动。删除数据卷会清除其中的数据库。

## 接入 Sites

1. 使用 `deploy/site/` 作为 Sites 前端源码。保留所用 Sites 项目生成的部署配置。
2. 将 `RAG_BACKEND_URL` 设置为后端 HTTPS 根地址。将 `RAG_BACKEND_TOKEN` 存为 Sites secret，值与后端的 `KB_ACCESS_TOKEN` 一致。
3. 按访问范围配置站点权限，通过 Sites 的发布流程部署。前端代理仅转发已声明的业务接口，服务端凭据由 Worker 持有。

## 验证

在 `deploy/site/` 中执行 `node scripts/build.mjs` 和 `node scripts/verify.mjs`，验证构建、代理和凭据转发。部署后检查文档列表和入库任务状态，再用已入库的文档验证混合检索和引用。API 与数据库检查见 [后端服务](backend.md#测试)。
