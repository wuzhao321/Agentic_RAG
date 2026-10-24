# 公开数据来源与处理

## 来源

| 来源 | 纳入内容 | 处理 |
| --- | --- | --- |
| [FastAPI 官方仓库](https://github.com/fastapi/fastapi/tree/69ddc2badf9da1123bba4d360225ae6b085985f6/docs/zh/docs/tutorial) | 请求体、查询参数、路径参数、响应模型、错误处理 | 下载固定版本 Markdown 和引用的 Python 代码；转 DOCX，省略图片；MIT 许可 |
| [浙江省税务局采购公告](https://zhejiang.chinatax.gov.cn/art/2026/7/31/art_11895_658092.html) | GPU 算力服务租用合同公开字段 | 保存网页摘录快照，转为带文本层和表格的 PDF |
| [海口市公共资源交易中心采购公告](https://ggzy.haikou.gov.cn/gonggao/92193) | AI+ 数字内容生产服务合同公开字段 | 保存网页摘录快照，转为 DOCX 表格 |

采购资料采用公告网页摘录，采集方式记录为 `retrieval_method=web search page extraction`。
转换文件包含公开公告字段与来源链接，不包含原始合同附件、手机号、地址或签名。
FastAPI 文档和示例代码遵循上游 MIT 许可，版权与许可文本保存在 [FASTAPI-LICENSE](FASTAPI-LICENSE)。

原始 Markdown 和示例代码缓存：`data/public_sources/`。
采购摘录快照：[procurement-snapshots.json](procurement-snapshots.json)。
转换后文件：`data/raw/public_demo/`。完整来源清单和 SHA-256：`data/demo/manifest.json`。

## 台账映射

`project_id` 对应公告项目编号；`project_name` 保留公告名称；`signed_date` 对应合同签订日期，
不是公告日期。`buyer` 和 `supplier` 分别为采购人和供应商，`owner` 为“未公开”。
`source_url` 在字段投影后仍保留，保证查询结果能追溯到原公告。

金额用 Decimal：浙江项目原值 `168.679200万元` 乘以 10000，得到 `1686792.00` 人民币元；
海口项目原值 `2,861,000.00元` 去掉千位分隔符后为 `2861000.00`。
原始金额字符串保存在 `amount_original`。海口公告的履约起始日期早于签订日期，
保持原文，不自行“纠正”或推测原因。

## 演示评估数据

`scripts/prepare_demo_eval.py` 生成 20 道 QA，其中 18 道来源问答、2 道无答案题，
并检查参考证据是否同时存在于来源快照、转换文档与索引片段中。
8 项 Agent 任务覆盖知识搜索、全文摘要、台账金额/日期筛选、供应商与未公开负责人。

证据定位结果写入 `data/demo/review.json`；`human_reviewed` 与 `formal_acceptance` 分别记录
人工审核和正式验收状态，生成数据的这两个字段均为 `false`。自动证据检查与模型指标分别保存。
演示数据用于开发检查，独立评估集的准备与运行流程见 [评估协议](../evaluation.md)。
