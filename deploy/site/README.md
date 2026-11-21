# 知序 · RAG 知识工作台

本目录提供 Sites 网页与同源接口代理。网页通过独立 Python 后端访问知识库、项目台账和评估资料，以及上传文档和管理入库任务。

## 构建与检查

在本目录运行：

```bash
node scripts/build.mjs
node scripts/verify.mjs
node scripts/preview.mjs
```

构建将前端资源嵌入 `dist/server/index.js`。预览监听 `http://127.0.0.1:4173`。后端变量为空时，网页显示未连接状态。

## 运行配置

| 变量 | 用途 |
| --- | --- |
| `RAG_BACKEND_URL` | 后端 HTTPS 根地址，例如 `https://rag-api.example.com` |
| `RAG_BACKEND_TOKEN` | Worker 的 secret，与后端 `KB_ACCESS_TOKEN` 相同 |

Worker 持有服务凭据并转发约定的业务接口。服务器配置、访问范围与迁移步骤见 [Sites 部署](../../docs/sites-deployment.md)。
