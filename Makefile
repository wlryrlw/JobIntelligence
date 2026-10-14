.PHONY: setup test eval eval-smoke eval-ablation eval-judge eval-judge-smoke rag-coverage rag-retrieval rag-answers rag-judge rag-eval backend frontend

RAG_RETRIEVAL_OUTPUT ?= eval/results/rag_retrieval.json
RAG_ANSWERS_OUTPUT ?= eval/rag/answers.jsonl
RAG_JUDGE_OUTPUT ?= eval/results/rag_judge.json
RAG_RETRIEVAL_TOP_K ?= 20
RAG_ANSWER_TOP_K ?= 8

setup:
	uv sync
	uv run python backend/etl.py --jobs data/fixtures/jobs_seed.json

test:
	uv run pytest

eval:
	uv run python -m eval.runner --dataset eval_test

eval-smoke:
	uv run python -m eval.runner --dataset seed --suite smoke

eval-ablation:
	uv run python -m eval.runner --dataset eval_test --ablation

eval-judge:
	uv run python -m eval.judge_runner --dataset eval_test --mode reference

eval-judge-smoke:
	uv run python -m eval.judge_runner --dataset seed --mode reference

rag-coverage:
	uv run python -m eval.rag.build_dataset

rag-retrieval:
	uv run python -m eval.rag.retrieval_runner --variant all --top-k $(RAG_RETRIEVAL_TOP_K) --output $(RAG_RETRIEVAL_OUTPUT)

rag-answers:
	uv run python -m eval.rag.answer_runner --top-k $(RAG_ANSWER_TOP_K) --output $(RAG_ANSWERS_OUTPUT)

rag-judge:
	uv run python -m eval.rag.judge_runner --answers $(RAG_ANSWERS_OUTPUT) --output $(RAG_JUDGE_OUTPUT)

rag-eval:
	$(MAKE) rag-coverage
	$(MAKE) rag-retrieval
	$(MAKE) rag-answers
	$(MAKE) rag-judge

backend:
	uv run python backend/run.py

frontend:
	npm --prefix frontend run dev
