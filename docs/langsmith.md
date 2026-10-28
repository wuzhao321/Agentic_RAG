# LangSmith 云端追踪

LangSmith 用调用树展示一次问答中的检索、模型生成、Agent 节点和工具调用，便于定位错误与耗时。它不替代 DeepSeek，也不替代人工评估。

## 账号与密钥

登录后根据网页域名确认区域：`smith.langchain.com` 为 US，`eu.smith.langchain.com` 为 EU，`apac.smith.langchain.com` 为 APAC。对应 API 地址依次为 `https://api.smith.langchain.com`、`https://eu.api.smith.langchain.com`、`https://apac.api.smith.langchain.com`。

在 Settings → API Keys → Create API key 中选择 **Tracing Only**，描述可填 `kb-rag-agent-demo`，选择当前工作区与适当有效期。此密钥只能写入追踪，不能读取已有轨迹。将密钥保存到本地 `.env`，避免提交到 Git。

在项目根目录 `.env` 的已有配置行填入：

```dotenv
LANGSMITH_API_KEY=替换为新建的追踪密钥
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_PROJECT=kb-rag-agent
LANGSMITH_TRACING=false
LANGSMITH_WORKSPACE_ID=
```

示例地址适用于 US；其他区域需替换。单一工作区可先留空工作区 ID；有多个工作区时填写 Settings 中目标工作区的 ID。

## 公开数据验证

在项目根目录运行：

```bash
.venv/bin/kb trace-demo
```

该命令在载入模型、核对索引和台账均属于公开来源清单后，仅在本次验证中打开追踪，上传两条固定公开测试任务的问答、检索证据、模型调用和工具轨迹。`LANGSMITH_TRACING=false` 保留为默认值，避免普通运行自动上传。

本地报告保存在 `data/demo/cloud-runs/*_langsmith_*/`。提交成功后，在网页 Tracing 中选择 `kb-rag-agent`，查找 `public-rag-agent-verification`，展开检索、RAG 生成和 Agent 工具调用。在网页中核对调用树、检索证据与工具轨迹是否完整。Tracing Only 密钥仅支持写入；读取接口校验需使用具备读取权限的凭据。

## 批量 Agent 任务追踪

显式加 `--trace` 可为每条公开开发任务上传独立调用树：

```bash
.venv/bin/kb validate-cloud --suite agent_baseline --trace
.venv/bin/kb validate-cloud --suite agent_retest --trace
```

任务结果逐条记录 `langsmith_trace_id`，运行目录的 `tracing.json` 汇总全部 trace ID、上传错误和网页核验状态。
追踪在核对公开索引与台账后才开启；结束后关闭上下文，不开启全局自动上传。
同步追踪的网络开销计入任务延迟，不能把此批次延迟与未开启追踪的基准直接比较。
此开关适用于公开 Agent 开发任务；任务完成率按 [评估协议](evaluation.md) 独立统计。

参考：[账号与密钥](https://docs.langchain.com/langsmith/create-account-api-key)、[追踪快速开始](https://docs.langchain.com/langsmith/observability-quickstart)。
