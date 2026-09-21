# OfferAgent 离线评测

评测只使用 `data/fixtures/jobs_seed.json` 和本地 DuckDB 临时目录，不调用真实 LLM、BOSS 直聘或外部网络。数据分为三路：

- `data/seed.jsonl`：少量冒烟样例，用于开发 runner；
- `data/eval_dev.jsonl`：开发集，包含展开后的 20 个 case；
- `data/eval_test.jsonl`：测试集，包含展开后的 30 个 case。

开发集和测试集合计 50 个 NL2SQL case。数据文件允许用 `repeat` 字段表达同一语义模板的不同回归样例，加载时会展开为带唯一 ID 的独立 case。

从项目根目录运行：

```bash
uv run python -m eval.runner --dataset eval_dev --output data/local/eval/dev.json
uv run python -m eval.runner --dataset eval_test --ablation --output data/local/eval/ablation.json
```

runner 会报告首轮准确率、纠错后最终准确率、恢复率、平均尝试次数、P50/P95 延迟，以及按 `characters / 4` 保守估算的 token 和成本。估算 token 不是供应商账单的精确值，结果文件会记录 runner、模型、数据集和配置版本。
