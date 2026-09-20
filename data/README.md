# 项目级数据目录

仓库中的可提交 fixture 和数据说明放在这里。运行时数据、登录态、Cookie、Chrome profile、未审查职位数据和任务 Runtime Store 不应提交到仓库；本地运行数据使用被 Git 忽略的 `data/local/` 或 `backend/data/local/`。

`fixtures/jobs_seed.json` 为可公开的人工合成样本。`databases/` 为 ETL 生成的本地分析数据，包含完整 JD 和证据，因此整个目录被 Git 忽略。不要将真实 CSV 或 `_schema.json` 复制到 fixture 目录。
