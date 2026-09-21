.PHONY: setup test eval eval-ablation backend frontend

setup:
	uv sync
	uv run python backend/etl.py --jobs data/fixtures/jobs_seed.json

test:
	uv run pytest

eval:
	uv run python -m eval.runner --dataset eval_test --output data/local/eval/eval_test.json

eval-ablation:
	uv run python -m eval.runner --dataset eval_test --ablation --output data/local/eval/ablation.json

backend:
	uv run python backend/run.py

frontend:
	npm --prefix frontend run dev
