# 检索消融方法

## 变体

| 配置 | 检索流程 |
| --- | --- |
| A | BGE 向量检索 |
| B | 向量检索 + BM25 + RRF 融合 |
| C | B + BGE reranker |
| D | C + 元数据路由与过滤回退 |

变体使用同一批知识片段、模型和 QA 数据，通过 `configs/retrieval.yaml` 的 `mode` 选择。
显式过滤条件在回退时保留，只有自动推断的过滤条件可以放宽。

## 执行

```bash
kb eval-rag
kb eval-rag --grid
```

普通模式在调参集上依次执行 A/B/C/D。网格搜索针对 D，组合候选数量 10/20/30、
RRF k 为 30/60/100、查询前缀开关，共 18 组配置。
配置建议按有效样本完整覆盖的三项 RAGAS 指标均值排序，结果保存到 `recommendation.json`。

## 对比口径

- 可回答题计算 Faithfulness、Answer Relevancy、Context Recall 的均值、有效样本数与 bootstrap 区间。
- 无答案题单独统计拒答率，评分失败保留错误与空值。
- 比较配置时保持语料、数据划分、模型版本及统计口径一致，结合延迟和失败案例分析。
- 调参后冻结配置与索引，使用独立留出集执行最终 A/D 对比。

数据格式、产物目录与冻结命令见 [评估协议](evaluation.md)。
