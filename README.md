# OfferAgent

OfferAgent 是一个面向求职决策的 Multi-Agent 项目，计划融合 BOSS 直聘岗位采集能力与 AskData 的自然语言问数能力，逐步形成“岗位采集 → 数据整理 → 自然语言分析 → 求职建议”的本地化工作流。

当前仓库处于项目整合和阶段 0 工程 Spike 阶段。本轮已统一两个已有项目的工程边界和 Python 运行环境，并建立了可恢复的任务契约与 SSE 假任务流；依赖由根目录配置统一解析并锁定。

## 项目结构

```text
OfferAgent/
├── backend/                       # AskData 后端：FastAPI、LangGraph、DuckDB/NL2SQL
├── frontend/                      # AskData 前端：Vue 3、Vite
├── integrations/
│   └── boss_zhipin/               # BOSS 直聘 CDP 采集与职位摘要工具
├── data/
│   └── local/                     # 本地运行数据，不提交到仓库
├── docs/
│   ├── Design document.md         # 统一项目设计文档
│   └── legacy/                    # 两个源项目的历史文档
├── pyproject.toml                 # Python 依赖唯一真源（后端 + 集成 + 测试）
├── uv.lock                        # Python 依赖唯一锁文件
├── .python-version                # 统一 Python 版本：3.11
├── README.md
└── LICENSE
```

## 环境要求

- Python 3.11（后端、测试和 integrations）
- Node.js 20.19 或更高版本（前端）
- 后端所需的大模型、Embedding 和 Rerank API Key
- BOSS 采集工具另需可用的 Chrome CDP 环境和用户授权的登录态

## 启动 AskData 应用层

以下命令均从项目根目录执行。

### Python 环境

所有 Python 依赖（后端、测试和 BOSS 集成）由根目录的 `pyproject.toml` 和 `uv.lock` 统一管理。macOS、Linux 和 Windows 均从项目根目录执行：

```bash
uv sync
```

请先编辑 `backend/.env` 并填写所需 API Key。首次配置可以复制模板：

```bash
cp backend/.env.example backend/.env
```

### 后端

```bash
uv run python backend/run.py
```

默认配置和完整配置说明见 [AskData 历史说明](docs/legacy/AskData.md)。

### 前端

```bash
npm --prefix frontend ci
npm --prefix frontend run dev
```

启动后可访问：

- 前端页面：<http://127.0.0.1:5173>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

### 阶段 0 任务流 Spike

阶段 0 提供不依赖真实 LLM 和 scraper 的假任务，用于验证任务状态、结构化事件、SSE 断线恢复和结果查询：

```bash
# 创建任务
curl -X POST http://127.0.0.1:8000/api/tasks \
  -H 'content-type: application/json' \
  -d '{"text":"验证任务流","session_id":"demo"}'

# 使用返回的 task_id 订阅事件
curl -N -H 'Accept: text/event-stream' \
  http://127.0.0.1:8000/api/tasks/<task_id>/events

# 页面刷新或 SSE 不可用时查询同一任务快照
curl http://127.0.0.1:8000/api/tasks/<task_id>
```

Runtime Store 会将任务快照写入 `backend/data/local/runtime/tasks/*.json`，将事件追加写入 `backend/data/local/runtime/events/*.jsonl`；这些运行时文件已被 Git 忽略。SSE 客户端应保存服务端返回的 `id`，重连时通过 `Last-Event-ID` 请求后续事件。

## BOSS 直聘集成

BOSS 直聘工具位于 `integrations/boss_zhipin/`，保留原项目的 CDP 被动捕获、城市码表、职位列表/详情采集和职位摘要能力。其详细使用说明见 [BOSS 直聘集成说明](integrations/boss_zhipin/README.md)。

集成与后端共用根目录 Python 环境，不再单独维护 `pyproject.toml`、`uv.lock` 或 `requirements.txt`。例如：

```bash
uv run python integrations/boss_zhipin/scripts/boss_cdp_raw.py --check
uv run python integrations/boss_zhipin/scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3
```

Python 测试统一通过根目录执行：

```bash
uv run pytest
```

本地采集结果应放在 `data/local/boss_zhipin/job-result/`，该目录已被 Git 忽略。请勿提交登录态、Cookie、Chrome profile、未审查的职位数据或其他敏感信息。

## 设计文档

- [OfferAgent 设计文档](docs/Design%20document.md)
- [AskData 历史说明](docs/legacy/AskData.md)
- [BOSS 直聘集成说明](integrations/boss_zhipin/README.md)

## 来源项目

- [AskData](https://github.com/wlryrlw/AskData)：提供 FastAPI、LangGraph、DuckDB/NL2SQL 和 Vue 前端基础能力。
- [boss-zhipin-scraper](https://github.com/eatmoreduck/boss-zhipin-scraper)：提供基于 Chrome CDP 的 BOSS 直聘职位采集与摘要能力。

## 项目边界

- 本项目不自动绕过验证码或风控，不伪造指纹，不使用代理池。
- 不自动发送消息、自动投递或执行未经用户确认的求职动作。
- 简历与职位数据默认按本地敏感数据处理，公开仓库只应使用脱敏或合成样本。
- 当前目录结构整理不会自动完成两个源项目的业务融合；后续正式开发以 [设计文档](docs/Design%20document.md) 中的 Agent 契约和开发计划为准。

## 阶段一：岗位 ETL 与数据层

用人工合成数据离线体验（不访问 BOSS 或 LLM）：

```bash
uv run python backend/etl.py --jobs data/fixtures/jobs_seed.json
uv run python backend/run.py
```

ETL 生成 `data/databases/jobs_market/` 下四张 CSV 表、`_schema.json`、`_quality.json` 与 `_manifest.json`；该路径指向原子发布的数据版本。原始输入归档与生成数据均被 Git 忽略。真实采集数据可以通过 `--jobs`、`--details` 导入，重复输入幂等，后续批次增量合并。避免在同一库中混合演示与真实市场数据。

默认本地 API/新浏览器使用单一岗位数据权限，旧示例账号仍可用于回归。ETL 后重启后端刷新 Schema 画像。完整字段映射、薪资规则、数据血缘、失败行为和当前边界见 [阶段一说明](docs/notes/1.md)。
