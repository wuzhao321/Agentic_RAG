# Dify 对照实验

此项使用本机 Docker 引擎与 Dify `1.18.0`（提交
`50abea97062110044f15d0e03ccecadd9ccc87ca`）；部署目录为 `.tools/dify/docker`。
[官方源码](https://github.com/langgenius/dify/tree/1.18.0/docker)中的 Compose 文件与服务端 Workflow API 是本页依据。

## 部署

安装并启动 Docker Desktop 后，在项目根目录操作：

```bash
git clone --depth 1 --filter=blob:none --sparse --branch 1.18.0 https://github.com/langgenius/dify.git .tools/dify
git -C .tools/dify sparse-checkout set docker
cd .tools/dify/docker
cp .env.example .env
```

已存在源码或 `.env` 时跳过对应命令，不覆盖配置。将 Dify 自己的 `.env` 中端口改为：

```dotenv
EXPOSE_NGINX_PORT=8088
EXPOSE_NGINX_SSL_PORT=8443
EXPOSE_PLUGIN_DEBUGGING_PORT=5003
```

同目录创建 `docker-compose.override.yaml` 限制本机访问（Compose 2.24.4+）：

```yaml
services:
  nginx:
    ports: !override
      - "127.0.0.1:8088:80"
      - "127.0.0.1:8443:443"
  plugin_daemon:
    ports: !override
      - "127.0.0.1:5003:5003"
```

`EXPOSE_PLUGIN_DEBUGGING_PORT` 也传给 API 的整数配置，不能把它写成 `host:port`。
初始化前设置 `SECRET_KEY`、数据库与 Redis 密码、Weaviate/插件内部密钥为随机值；
`CODE_EXECUTION_API_KEY` 与 `SANDBOX_API_KEY` 保持一致。
`CELERY_BROKER_URL` 中的密码也必须与 `REDIS_PASSWORD` 一致（URL 转义后填入），
不能保留示例中的默认密码，否则文档上传成功但后台索引任务提交失败。
`WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS` 必须与 `WEAVIATE_API_KEY` 一致。
本机部署关闭 Weaviate 匿名访问与遥测。修改后重建相应服务以加载配置。
设置非空随机 `DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN` 与 `DIFY_AGENT_API_TOKEN`；
`DIFY_AGENT_SERVER_SECRET_KEY` 必须用 `secrets.token_urlsafe(32)` 生成无填充 base64url。
然后执行
`docker compose -p kb-dify up -d`。打开 `http://127.0.0.1:8088/install` 完成管理员初始化。
Dify 与本项目根目录 `.env` 是两份不同配置文件。

## 对照 Workflow

本机向量服务：运行 `.venv/bin/python -m kb.embedding_server`，仅监听
`127.0.0.1:8766`。首次启动生成 `.local/dify-embedding.token`（权限 0600，勿提交）。
在官方 OpenAI-API-compatible 插件添加 Text Embedding 模型：
名称 `bge-large-zh-v1.5`，地址 `http://host.docker.internal:8766/v1`，
密钥使用上述本机 token，批大小 16、上下文 512，文档/查询前缀留空。
使用与主项目相同 revision 的本地 BGE 模型；接口支持 float/base64 向量、
限制请求大小、拒绝浏览器 Origin，不记录问题或密钥。
本机服务必须与 Docker 同时运行。它不提供生成模型接口。

Dify 对照知识库使用 512 字符分段、64 字符重叠、Top K=5、纯向量检索、
不重排、不设分数阈值。Dify 的字符切分和解析器与主项目不完全相同，
实验报告必须记录此差异，不能宣称只改变了检索算法。
采用单换行 `\n` 分段时，短行可能被拆成独立片段，影响表格字段关联。
对比不同切分参数时分别保存配置和结果。

可用以下命令生成无凭据的四节点 DSL（以实际知识库 ID 替换参数）：

```bash
.venv/bin/python scripts/prepare_dify_workflow.py <dataset-id> --output .local/dify-public-demo.yml
```

在工作室导入后检查并发布。生成器按 Dify 1.18.0 官方节点定义编写；
DSL 版本兼容提示不等同于执行失败，必须用真实试运行验证。

1. 添加 DeepSeek 模型供应商，使用已授权的 API；关闭思考模式、temperature=0，记录实际模型名。
2. 将 `data/raw/public_demo` 的原始 7 份演示转换文档导入知识库，记录切分、embedding、召回与重排参数。
3. 新建 Workflow：Start 输入字符串 `query` → Knowledge Retrieval 使用 `query` → LLM → End。
4. LLM 使用 `{{#retrieval.result#}}` 的完整来源记录（包括 title、metadata.document_name、content）回答，
   同一项目的字段只从对应文档取值，金额保留原始单位，不自行追加换算。
   不使用仅包含正文的默认 context；无依据只输出“知识库中未检索到相关内容”。
5. End 输出字符串 `answer`；如导出证据，另加 `retrieved_contexts`。发布 Workflow，取得应用 API 密钥。
6. 导出实际 DSL 到本地运行记录，保留真实知识库 ID 和配置；不要提交凭据。

本项目根目录 `.env` 填写：

```dotenv
DIFY_API_BASE=http://127.0.0.1:8088/v1
DIFY_APP_KEY=已发布Workflow的应用密钥
```

发布后导出实际配置，再运行评估：

```bash
.venv/bin/python scripts/snapshot_local_dify.py <app-id> <dataset-id> --output .local/dify-config.json
.venv/bin/python scripts/evaluate_dify.py --workflow-snapshot .local/dify-config.json
```

快照工具读取指定本机应用的已发布版本和知识库，导出 JSON 与无凭据 YAML；
不会导出供应商密钥。评估要求七份文档均已索引完成，并校验每次返回的 workflow_id。
逐条结果、延迟、token 数、实际检索证据、配置快照和审核模板保存到
`data/demo/dify-runs/`。接口执行成功与答案正确分开统计；人工记录关键词、表格问题的实际失败案例。
失败案例按实际结果记录问题、证据与错误类型。

docx2txt 与结构化解析对照文件位于 `data/review/public-v1/table_comparison_20.md`。
解析结构对照与 Dify 问答效果分别评估。
