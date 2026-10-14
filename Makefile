.PHONY: setup test eval eval-smoke eval-ablation eval-judge eval-judge-smoke backend frontend

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

backend:
	uv run python backend/run.py

frontend:
	npm --prefix frontend run dev
