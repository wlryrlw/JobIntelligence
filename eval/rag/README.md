# 岗位 JD RAG 评测

本目录只评测岗位 JD RAG，不改变现有 `data_qa`。数据边界来自当前已发布快照：
`data/databases/jobs_market/jobs.csv` 和 `job_details.csv`。

## 1. 校验评测集和当前数据覆盖

评测集中的 `source_dataset_version`、`relevant_job_ids` 和过滤条件会与当前快照逐项校验。当前快照只有上海、杭州，没有北京；因此北京/深圳 case 是 `no_data` 负例，不参与普通 Recall 计算，而是检查系统是否正确拒答或说明数据不足。

```bash
.venv/bin/python -m eval.rag.build_dataset
```

这会生成 `data_coverage.json`，其中只保存数据版本、样本量、城市/经验覆盖和源文件摘要，不保存 JD 原文。

## 2. 检索效果与 A/B/D 消融

三组定义固定为：

- A：BM25；
- B：Embedding；
- D：BM25 + Embedding + RRF + Rerank。

不增加 `BM25 + Embedding + RRF` 组。三组使用同一个 Golden Set、同一个当前数据版本和相同的过滤条件。D 组要求 `RAG_RERANK_ENABLED=true` 且配置 `RAG_RERANK_MODEL`。

```bash
.venv/bin/python -m eval.rag.retrieval_runner \
  --variant all \
  --output eval/results/rag_retrieval.json
```

输出指标包括 Recall@5/10/20、Precision@8、MRR@10、nDCG@10、无数据 case 正确率和过滤越界率。每个 case 还保留去重后的 job_id 排名，便于定位错误。

## 3. Qwen LLM-as-judge

答案评测集是 JSONL，每行至少包含以下字段：

```json
{
  "case_id": "sh_35_rag_basics",
  "query": "上海 3-5 年 Agent 岗位通常如何要求 RAG？",
  "answer": "实际运行 CareerAgent 后得到的回答",
  "evidence": [
    {
      "chunk_id": "...",
      "job_id": "...",
      "title": "岗位标题",
      "company": "公司",
      "city": "上海",
      "experience": "3-5年",
      "snippet": "从 RetrievedJd.public_source() 得到的短证据"
    }
  ],
  "required_aspects": ["RAG", "Agent架构"],
  "answerable": true
}
```

使用已有独立 Qwen Judge 配置 `JUDGE_API_KEY`、`JUDGE_BASE_URL`、`JUDGE_MODEL`，不会使用主 DeepSeek 的 `LLM_*` 配置：

`answer_runner` 会通过主 `LLM_*` 模型生成与线上岗位要求问答相同类型的综合解读，因此运行它前需要配置 `LLM_API_KEY`。
`CAREER_LLM_ENABLED` 不影响这条岗位要求总结链路。

```bash
.venv/bin/python -m eval.rag.answer_runner \
  --output eval/rag/answers.jsonl

.venv/bin/python -m eval.rag.judge_runner \
  --answers eval/rag/answers.jsonl \
  --output eval/results/rag_judge.json
```

Judge 只依据传入的 JD evidence 评分 `faithfulness_score` 和 `relevance_score`，分值为 0/1/2，并记录 unsupported claims、missing aspects。完整结果不记录 API Key，也不要求发送完整简历或完整 JD 原文。
