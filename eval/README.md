# JobIntel 离线评测

确定性评测只使用 `data/fixtures/jobs_seed.json` 和本地 DuckDB 临时目录，不调用真实 LLM、BOSS 直聘或外部网络。数据分为三路；可选的 LLM-as-judge 是独立命令，不会被确定性 runner 或 pytest 自动调用：

- `data/seed.jsonl`：6 个固定冒烟样例；
- `data/eval_dev.jsonl`：展开后的 24 个开发 case；
- `data/eval_test.jsonl`：展开后的 100 个测试 case。

开发集和测试集合计 124 个 NL2SQL case。`repeat` 用于表达同一语义模板的多个回归样例，加载时会展开为带唯一 ID 的独立 case；因此展开数量适合衡量回归覆盖，不等价于 124 个完全独立的泛化样本。

每个 case 可以声明 `category`、`tags` 和 `smoke`。`--suite smoke` 只运行标记为 smoke 的样例，`--suite full`（默认）运行所选数据集的全部样例。

从项目根目录运行：

```bash
uv run python -m eval.runner --dataset seed --suite smoke
uv run python -m eval.runner --dataset eval_dev --suite full
uv run python -m eval.runner --dataset eval_test --ablation
make eval-smoke
```

未指定 `--output` 时，确定性评测结果默认写入 `eval/results/`：

- 普通评测：`eval/results/{dataset}_{suite}.json`；
- 消融评测：`eval/results/{dataset}_{suite}_ablation.json`。

命令行会打印整体评测指标和结果文件路径，但不会展开每个 case 的完整明细。完整 case 结果仍保存在 JSON 文件中。需要自定义位置时仍可传入 `--output`。

控制台字段会追加中文说明，例如 `dataset（数据集）`、`dataset_size（数据集规模）`、`final_accuracy（最终准确率）`。JSON 文件中的字段名保持英文，便于程序读取和下游处理；`SQL`、`schema`、`score`、`tokens`、`few-shot` 等专有名词保留原文。

## 可选的 LLM-as-judge

Judge 使用独立的 API 配置和模型，每个 case 只评测一个最终答案。当前确定性数据契约中的 `repair_sql` 是纠错流程结束后的最终 SQL，Judge runner 将它作为 `final_answer`；`first_sql` 和 `few_shot_sql` 不参与 Judge 评测，也不会作为命令行选项暴露。`expected_sql` 始终是参考答案。

Judge 支持两种模式：

- `single`：只把用户问题和最终 SQL 交给 Judge，进行独立评分；
- `reference`：额外提供 `expected_sql` 以及最终 SQL、参考 SQL 的本地执行结果，进行参考答案对照评测。

默认的 Judge Prompt 位于：

- `eval/prompts/sql_single_judge.txt`；
- `eval/prompts/sql_reference_judge.txt`。

也可以通过 `--prompt` 或 `--prompt-file` 传入自定义 Prompt。Judge 只要求返回 `score`、`label`、`error_type` 和简短 `reason`。`score` 只能是整数 `0`、`1` 或 `2`：`0` 表示错误，`1` 表示部分正确，`2` 表示正确；只有 `score=2` 才会生成 `passed=true`。

Judge 的 API 配置不回落到主问答模型，推荐在 `backend/.env` 中设置：

```bash
JUDGE_API_KEY=your-judge-api-key
JUDGE_BASE_URL=https://api.example.com/v1
JUDGE_MODEL=your-judge-model
JUDGE_TIMEOUT=60
JUDGE_TEMPERATURE=0
JUDGE_MAX_RETRIES=1
JUDGE_PROMPT_FILE=eval/prompts/sql_reference_judge.txt
```

也可以使用 `JUDGE_PROMPT` 直接传入 Prompt；命令行的 `--prompt`、`--prompt-file` 优先级高于环境变量。

执行参考答案评测：

```bash
uv run python -m eval.judge_runner \
  --dataset eval_test \
  --mode reference \
  --prompt-file eval/prompts/sql_reference_judge.txt \
  --output eval/results/judge_reference.json
```

执行单答案评分：

```bash
uv run python -m eval.judge_runner \
  --dataset eval_test \
  --mode single \
  --prompt-file eval/prompts/sql_single_judge.txt \
  --output eval/results/judge_single.json
```

也可以使用 `make eval-judge` 执行默认的 reference 评测。Judge 结果单独写入 `JudgeRunResult`，不会覆盖或改写确定性评测中的 `final_accuracy`、执行成功率和 Schema 指标。

未指定 `--output` 时，Judge 结果默认写入 `eval/results/judge_{dataset}_{mode}.json`，例如 `eval/results/judge_eval_test_reference.json`；命令行会打印 Judge 汇总指标和结果文件路径，不会展开每个 case 的完整明细。

Judge 会按 case 串行调用外部 API。执行开始时会立即打印总 case 数，并在每个 case 开始、完成或失败时打印进度；因此 `make eval-judge` 默认会处理 `eval_test` 的 100 个 case，耗时取决于 Judge API 响应速度。首次配置或联调时可以先运行 `make eval-judge-smoke`，它只评测 `seed` 的 6 个固定 case。

## 指标

### Schema 使用指标

当前离线 runner 以 `expected_sql` 引用的表/字段作为 Gold，以实际生成 SQL 引用的表/字段作为预测，计算宏平均：

- `first_schema_precision` / `first_schema_recall` / `first_schema_f1`：首轮 SQL 的字段 Schema 指标；
- `final_schema_precision` / `final_schema_recall` / `final_schema_f1`：最终 SQL 的字段 Schema 指标。

同时输出 `first_schema_table_*` 和 `final_schema_table_*` 表级指标；不带 `table` 的字段是字段级主指标。

每个 case 还会保存同样的 `first_*` 和 `final_*` 指标。汇总结果同时保留表级和字段级指标，便于区分“选对表但字段错了”和“连表都没有选对”。

这组指标衡量“SQL 实际使用的 Schema 是否覆盖 Gold SQL 所需 Schema”，对应 QueryAgent 使用完整 SchemaCatalog 生成 SQL 的链路，不是字段检索模型的泛化评测。当前离线 runner 使用确定性 SQL fixture，保持无网络、无 token 消耗。

### SQL 执行与查询结果指标

- `first_execution_success_rate`：首轮 SQL 是否成功通过校验并执行；
- `final_execution_success_rate`：纠错流程结束后，case 是否至少成功执行并返回结果；
- `first_query_result_success_rate`：首轮返回结果是否与 Gold 结果一致；
- `final_query_result_success_rate`：最终返回结果是否与 Gold 结果一致；
- `recovery_rate`：首轮结果错误的 case 中，最终被纠正成功的比例；
- `total_sql_executions` / `successful_sql_executions`：最终流程内实际调用数据库的次数和成功次数。

原有的 `first_pass_accuracy` 和 `final_accuracy` 仍然保留，分别与 `first_query_result_success_rate` 和 `final_query_result_success_rate` 相同，用于兼容已有结果和测试。

SQL 执行成功不代表查询结果正确。例如 SQL 可以成功执行，但使用了错误的过滤条件；这种 case 会计入执行成功率，但不会计入查询结果成功率。

### 延迟与成本指标

- `average_sql_execution_ms`：所有实际 SQL 执行尝试的平均耗时；
- `p50_sql_execution_ms` / `p95_sql_execution_ms`：纯 SQL 执行耗时的 P50/P95；
- `p50_latency_ms` / `p95_latency_ms`：包含 Schema、生成、校验、纠错和执行的 QueryAgent 端到端耗时；
- `total_estimated_tokens` / `total_estimated_cost`：按字符数除以 4 的可复现估算，不是供应商账单中的真实 token usage。

`sql_execution_ms` 由评测边界包裹 `DuckDbEngine.execute()` 记录，不把期望 SQL 的执行耗时计入结果。

## 数据和结果边界

当前确定性评测验证的是 SQL 校验、Schema 使用、执行、结果比较、纠错、耗时统计和消融链路。可选 Judge 用于语义评分，不是确定性结果的替代品，也不应把它放入普通 smoke 或 pytest 流程。Judge 失败会作为 case 级 `failed` 结果记录，不会静默转成通过。
