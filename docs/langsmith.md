# LangSmith 追踪

LangSmith 以调用树展示检索、生成、Agent 节点和工具调用。全局追踪默认关闭。项目的专用验证命令在检查公开来源清单后，仅为对应调用开启追踪。

## 配置

按所用 LangSmith 部署区域填写 `.env`。以下示例保留全局追踪关闭，密钥与工作区信息在使用者的本地环境中设置。

```dotenv
LANGSMITH_API_KEY=
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_PROJECT=kb-rag-agent
LANGSMITH_TRACING=false
LANGSMITH_WORKSPACE_ID=
```

验证写入可使用仅追踪权限的密钥。读取已有追踪需使用有读取权限的凭据。追踪可能包含问题、检索证据和工具输入输出。

## 验证

准备并入库 [公开演示数据](demo.md)，然后执行：

```bash
uv run --no-sync kb trace-demo
uv run --no-sync kb validate-cloud --suite agent_baseline --trace
```

`trace-demo` 运行固定的公开问答和工具任务。`--trace` 可用于公开 Agent 开发集的逐条追踪。运行目录位于 `data/demo/cloud-runs/`。按报告中的追踪 ID 核对调用树及其各阶段。同步上传会计入请求耗时，做延迟对比时应保持追踪配置一致。

模型和工具调用的评估方法见 [评估协议](evaluation.md)。
