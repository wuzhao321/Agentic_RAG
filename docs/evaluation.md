# 评估协议与命令

## QA 标注与划分

QA 数据采用 JSONL，每行一条记录。字段示例：

```json
{"id":"example-001","question":"CreateOrder 超时是多少？","reference_answer":"3000 毫秒","reference_contexts":[{"doc_id":"document-id","section_path":"接口参数"}],"doc_type":"接口说明书","project_name":"演示项目","question_type":"表格类"}
```

基准协议使用 200 条 QA，关键词/标识符类和表格类各至少 25%，无答案类约 10%。问题、答案和出处经人工审核后用于正式评估。
`kb split-eval data/eval/qa_all.jsonl` 使用种子 42，按题型分层为 160/40，禁止覆盖已有结果，留出文件只读。
无答案题的 question_type 使用 `无答案类`，参考答案建议为 `知识库中未检索到相关内容`。
输入空集、ID 重复会报错；划分还拒绝完全重复的问题。近义重复与同源泄漏仍需人工复核。

## RAGAS 调参

```bash
kb eval-rag
kb eval-rag --grid
```

普通运行依次使用 A/B/C/D，使用同一调参数据、模型和当前索引。
网格仅针对 D：top-N 为 10/20/30，RRF k 为 30/60/100，查询前缀开关，共 18 组。
建议配置按三项指标等权均值排序，只比较有效样本完整覆盖的配置，不自动改写 YAML；还应结合拒答、延迟和失败案例选择。

RAGAS 使用 Faithfulness、ResponseRelevancy（字段 answer_relevancy）、LLMContextRecall。
生成和裁判共用配置选择的模型，包装器不改变 temperature=0，也不把客户端 n 改成多候选。
裁判 embedding 使用本地 BGE，RAGAS 遥测关闭。RAGAS 固定为 0.3.9，Community 版本由锁文件约束；
升级评估依赖时运行集成测试，检查指标适配与结果保存。

统计口径：

- 三项 RAGAS 只在可回答题上计算，报告 eligible_n、valid_n、均值和 bootstrap 95% 区间。
- 无答案类单独计算拒答率，不把无定义的指标强行算成 0 或 1。
- 生成/评分失败保留 null 与错误类别；未覆盖全部可回答样本时不判断指标达标。
- 无答案题生成失败仍保留在拒答率分母中，按失败计。
- 引用校验导致拒答时仍保留实际检索上下文，以区别检索和生成失败。
- 裁判上下文包含生成时使用的来源编号、文档/章节标签和正文，逐字保持一致；
  比较不同运行时，应确认上下文格式一致。
- 跨配置和跨数据集比较使用相同的样本范围、失败处理规则与指标定义。

## 最终评估

```bash
kb freeze-eval
kb eval-rag --final
```

冻结四份配置、模型供应商与模型名、BGE revision、代码和 uv.lock 哈希、调参集哈希、领域词典哈希与索引 fingerprint。
冻结不读取留出集，冻结文件只写一次。最终运行前逐项比对，发生变化即拒绝。

`holdout-final.lock` 位于留出集真实路径旁边，先创建锁再读取文件；同次运行 A/D。
锁在成功、失败或中断后都保留，不自动重试。禁止 --final 与 --grid 同用。
调参路径通过软链接或硬链接指向留出集也会被拒绝。这些是正常流程的防误用措施，不是文件系统权限沙箱。
不得手工绕过锁后反复调参。

## 运行产物

每次创建 `data/eval/runs/<UTC时间>_<类型>_<唯一ID>/`：

- snapshot.json、environment.json、dataset.json：配置、依赖、机器、数据哈希与数量。
- status.json：running、complete、completed_with_errors 或 failed。
- A.config.json 等：该变体的实际检索配置。
- 各变体目录内 generations.jsonl：调用裁判前立即保存的生成结果。
- results.jsonl、summary.json 与 report.md：逐条评分、汇总和本次配置对比。
- 网格另有 recommendation.json，只给出完整覆盖的配置排名。

运行结果保存在 Git 忽略目录。报告同时记录样本量与数据划分，区分调参集和留出集。
人工裁判复核及其与自动裁判的一致率单独记录。

## Agent 数据与审核

首批文件为 agent_tasks_40.jsonl，新任务复测文件为 agent_tasks_retest_20.jsonl。字段示例：

```json
{"id":"agent-example-001","question":"演示项目由谁负责？","expected_tools":["ProjectQueryTool"],"expected_args":[{"project_name":"演示项目","fields":["owner"]}],"answer_points":["测试负责人"]}
```

id 可省略并按问题生成。工具名、参数列表必须对应，预期参数也经过 Pydantic 校验。
复测拒绝与首批相同的 ID 或问题。每条任务使用新的 thread_id。

工具选择按顺序比较；参数经各工具 schema 规范化，例如 Decimal 的 100 与 100.00、可选字段缺省与 null 视为等价。
选错工具时参数比较为 null，单独报告 parameter_unknown_n。
参数错误率为已发现错误任务数 / 总任务数；存在未知项时只是下界，不能判断 ≤5% 达标。
超步数和执行异常保存最后可见状态；选错工具、参数错误和工具异常自动归因，检索/生成错误与任务完成由人工审核。

复制运行目录的 reviews.template.jsonl 并填写：

```json
{"id":"agent-example-001","completed_human":true,"failure_human":null,"notes":"答案覆盖要点且有证据"}
```

`kb review-agent <运行目录> <已填写审核文件>` 支持分批导入；同一 ID 更正会保留新版本。
原始 results.jsonl 不变。全部审核后才给出 completion_rate，部分审核只给 reviewed_completion_rate 和 reviewed_n。
未审核值保持 null，不默认成功。

## 自动化测试

自动化测试使用临时合成数据和结构化模型替身，RAGAS 库负责解析和计算，覆盖评估控制流、
指标适配和结果保存。模型效果评估通过上述运行命令执行，独立保存数据、配置与实际模型输出。

## 公开开发集检查

以下命令只使用 `data/demo` 开发数据，不读取正式留出集：

```bash
python scripts/smoke_cloud.py
kb validate-local --suite retrieval
kb validate-cloud --suite ragas
kb validate-cloud --suite conversation
kb validate-cloud --suite agent
```

云端检查使用 `.env` 中的供应商凭据，运行产物存入 `data/demo/cloud-runs/`。
检索检查覆盖 A–D、18 组网格和演示配置，保存在 `data/demo/validation/`；
参考片段命中率与 RAGAS 指标分别报告。开发集检查与正式评估分别使用独立的数据路径和审核流程。
RAGAS 在同一事件循环中复用异步客户端，避免逐题评分后关闭循环导致连接失效。

## 公开候选集的人工审核

运行 `scripts/prepare_extended_public.py` 生成候选集，按需运行
`scripts/refine_public_qa.py` 用配置模型细化概念题，并逐字校验答案出处。
然后运行 `scripts/prepare_review_tasks.py` 更新审核页面和 Agent 候选任务。
再次运行原始数据生成脚本会重建候选题，因此应在人工审核开始前完成数据准备。

用浏览器打开 `data/review/public-v1/review.html`，逐题检查并导出 `qa-reviews.json`。
审核内容包括问题明确性、答案正确性、证据充分性、题型、重复题、同源题与无答案判定。
页面保存审批和备注；需要改题时先修改候选 JSONL，再生成页面并重新审核该版本。
未经审核不能把“来源逐字匹配”写成“业务审核通过”。

全部 200 题批准且填写审核人后：

```bash
uv run --no-sync python scripts/import_qa_reviews.py /实际路径/qa-reviews.json
uv run --no-sync kb split-eval data/eval/qa_all.jsonl
```

导入器核对候选文件哈希、全部审核 ID、逐条批准和题型配额，并保留审核记录。
它不自动划分数据；任何驳回、缺项或哈希变化都会阻止导入，不会覆盖已有正式数据。
正式留出仍遵循前述冻结与一次性运行协议。

`agent_baseline_40.jsonl` 与 `agent_retest_20.jsonl` 为互不重复的候选任务，按上述流程审核后用于任务完成率评估。
可先运行 `kb validate-cloud --suite agent_baseline` 和 `--suite agent_retest` 做开发验证。
工具参数按严格规范化相等比较；同义检索词和项目简称也可能被标记不一致，应结合人工审核解释，
不得把严格字符串匹配率直接称为业务完成率。

Dify 的部署、配置快照和评估步骤见 [Dify 对照步骤](dify.md)。

## 裁判人工复核

`python scripts/prepare_acceptance_pack.py` 为活动索引对应的已完成运行导出 Agent 逐条证据和
A/D 各10条裁判抽样（固定种子42），保存在 `data/review/public-v1/`。
复制 `judge_reviews.template.json` 为自己的审核文件，依据 `judge_review_20.md`
逐条填写三个0到1的评分、审核人和依据；应先独立评分，再查看自动分数。
完成20条后导入：

```bash
kb review-judge data/review/public-v1/judge_review_20.jsonl /实际路径/judge-reviews.json
```

一致率预定义为人工与自动分数绝对差不超过0.20的占比，同时报告平均绝对差和样本数。
此容差用于描述裁判一致性，不等于答案正确率或指标验收阈值。缺分、未署名、缺依据、重复ID
和样本哈希变化均拒绝导入。每次导入保存独立版本，不覆盖原始评分或先前审核。

云端追踪配置及公开数据验证见 [LangSmith 操作说明](langsmith.md)。
