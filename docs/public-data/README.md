# 公开演示数据及核对口径

## 来源

| 来源 | 纳入内容 | 处理 |
| --- | --- | --- |
| [FastAPI 官方仓库](https://github.com/fastapi/fastapi/tree/69ddc2badf9da1123bba4d360225ae6b085985f6/docs/zh/docs/tutorial) | 请求体、查询参数、路径参数、响应模型、错误处理 | 下载固定版本 Markdown 和引用的 Python 代码；转 DOCX，省略图片；MIT 许可 |
| [浙江省税务局采购公告](https://zhejiang.chinatax.gov.cn/art/2026/7/31/art_11895_658092.html) | GPU 算力服务租用合同公开字段 | 保存网页检索快照摘录，核对后转为带文本层和表格的 PDF |
| [海口市公共资源交易中心采购公告](https://ggzy.haikou.gov.cn/gonggao/92193) | AI+ 数字内容生产服务合同公开字段 | 保存网页检索快照摘录，核对后转为 DOCX 表格 |

采购公告的直接 HTTPS 下载在本次环境中失败，网页检索可以读取，因此明确记录
`retrieval_method=web search page extraction`。转换文件是公告摘录，**不是下载到的原始合同**。
只取公开业务字段，未将手机号、地址或签名导入知识库。不存在的技术参数或负责人不补写。
未纳入无法成功下载/可靠解析的候选政府年度报告。

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

## 演示评估集审核

20 道 QA 中，18 道回答题已由助手逐题对照来源；脚本再次验证参考证据同时存在于
原始来源、转换文档和当前索引中。2 道无答案题用于检查拒答。8 项 Agent 任务覆盖
知识搜索、全文摘要、台账金额/日期筛选、供应商与未公开负责人。

这属于**助手来源核对**，不属于独立业务人员的人工批准。统一记录
`human_reviewed=false`、`formal_acceptance=false`。核对结果不是模型测评分数；
模型测评另行保存真实输出。本批演示集可用于开发调试，不作为正式留出集。
