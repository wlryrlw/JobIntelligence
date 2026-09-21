# OfferAgent

OfferAgent 是一个面向个人求职决策的本地化 Multi-Agent 应用：把岗位采集、结构化 ETL、自然语言问数、简历证据分析和求职建议串成一条可审计、可复现的工作流。

当前仓库已完成阶段 0–4 的离线 MVP 主链路：工程契约、岗位 ETL、QueryAgent 自修正、CareerAgent 证据报告、离线评测和前端演示链路。本轮已统一两个已有项目的工程边界和 Python 运行环境，并建立了可恢复的任务契约与 SSE 假任务流；依赖由根目录配置统一解析并锁定。

## 软件定位与基本功能

OfferAgent 面向需要同时分析岗位市场和个人简历的求职者、研究者与面试演示场景，核心目标是让岗位结论、SQL 执行过程和简历证据都能被追溯。

- 岗位数据处理：将采集或合成的职位 JSON 经过字段归一化、薪资解析、城市/经验/学历/技能/福利抽取后，发布为带版本信息的 DuckDB/CSV 数据集。
- 自然语言问数：针对 `jobs_market` 岗位库注入完整 Schema，由 QueryAgent 生成只读 SQL，并在有限次数内完成 SQL 自修正。
- 简历与岗位证据分析：解析文本或 PDF 简历，先在本地做 PII 脱敏，再提取技能、项目、量化成果和目标岗位匹配证据，生成结构化建议。
- 可观测执行链路：保留 SQL 尝试、纠错原因、工作流步骤、图表/Markdown 结果和任务事件，便于调试、复盘和前端展示。
- 离线评测与演示：提供合成岗位数据、NL2SQL 评测集、确定性评测 runner、前端执行轨迹和 SSE 假任务流，不依赖真实爬虫或线上 LLM 即可复现。

仓库还保留旧 AskData 的多数据库、Schema 检索和 Embedding/Rerank 兼容路径，用于历史示例与回归；当前岗位市场主链路使用全量 Schema，不依赖该旧检索路径。普通 `/api/query` 是同步查询接口，`/api/tasks` 是阶段 0 的可恢复演示任务流，两者不是同一个生产任务队列。

## 项目结构

```text
OfferAgent/
├── backend/                       # OfferAgent 后端：FastAPI、LangGraph、DuckDB/NL2SQL
├── frontend/                      # OfferAgent 前端：Vue 3、Vite
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

主要代码目录如下：

```text
backend/app/
├── api/              # FastAPI 路由、鉴权与任务/SSE 接口
├── career/           # 简历解析、脱敏、证据提取与 CareerAgent
├── etl/              # 岗位字段归一化、质量检查与版本发布
├── querying/         # SchemaCatalog、QueryAgent 与 SQL 执行/纠错
├── retrieval/        # 旧 AskData Schema 检索兼容路径
├── security/         # 用户、工作区、访问范围与敏感数据控制
├── services/         # 查询服务、任务运行时与应用层编排
└── workflows/        # LangGraph 查询工作流和节点
frontend/src/
├── api.ts、types.ts  # API 客户端与 TypeScript 契约
└── components/       # 查询、澄清、报告、轨迹和 SSE 演示组件
eval/                 # 离线评测数据、模型、数据集和 runner
integrations/         # BOSS 直聘 CDP 采集集成
docs/                 # 设计文档、阶段说明和演示记录
```

## 环境要求

- Python 3.11（后端、测试和 integrations）
- Node.js 20.19 或更高版本（前端）
- 真实自然语言问数和 CareerAgent 建议层需要兼容 OpenAI API 的 `LLM_API_KEY`；离线 ETL、阶段 0 SSE 和确定性评测不需要 API Key
- Embedding/Rerank 配置只供保留的旧 AskData Schema 检索兼容路径使用，岗位市场的全量 Schema QueryAgent 不需要它们
- BOSS 采集工具另需可用的 Chrome CDP 环境和用户授权的登录态

## 环境配置与启动

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

离线 ETL、阶段 0 SSE 和确定性评测可以不配置真实模型；要启用 CareerAgent 的模型建议层，需在 `backend/.env` 中设置：

```bash
LLM_API_KEY=your-api-key
# 可选：LLM_BASE_URL=https://api.openai.com/v1
```

`backend/.env` 仅用于本地配置，已被 Git 忽略；不要把 API Key、Cookie、简历原文或 Chrome profile 提交到仓库。

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

## CareerAgent：简历与岗位分析

CareerAgent 支持文本和 PDF 简历的本地解析、PII 脱敏、技能/项目/量化成果抽取，以及基于 `jobs_market` 岗位样本的技能覆盖和求职建议。

1. 上传简历，默认使用本地匿名用户：

```bash
curl -X POST http://127.0.0.1:8000/api/resumes \
  -F "file=@/path/to/resume.pdf"
```

接口会返回 `resume_id`、解析状态和统计信息，不返回原始文件或完整简历文本。

2. 将返回的 `resume_id` 和目标岗位显式传给同步查询接口：

```bash
curl -X POST http://127.0.0.1:8000/api/query \
  -H 'content-type: application/json' \
  -d '{"query":"请基于我的简历分析 AI Agent 岗位的技能差距并给出建议","session_id":"career-demo","resume_id":"<resume_id>","target":{"keyword":"AI Agent","city":"上海","experience":"3-5年","degree":"本科"}}'
```

建议使用显式 `resume_id` 和 `target`，不要让模型从自然语言猜测简历或岗位上下文。若要启用额外的模型建议层，在 `backend/.env` 中设置 `CAREER_LLM_ENABLED=true` 并提供 `LLM_API_KEY`；关闭该开关只表示建议层使用确定性逻辑，调用自然语言 `/api/query` 仍需要 `LLM_API_KEY` 完成请求预处理。

3. 不再使用时删除本地简历记录：

```bash
curl -X DELETE http://127.0.0.1:8000/api/resumes/<resume_id>
```

简历上传受文件大小、页数和最小文本长度限制，具体值由 `RESUME_MAX_BYTES`、`RESUME_MAX_PAGES` 和 `RESUME_MIN_TEXT_CHARS` 控制。简历目录默认位于 `data/local/resumes/`，已被 Git 忽略。

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

## 阶段四：评测、前端与离线演示

阶段四默认不调用真实 LLM 或 live 爬取，便于面试时稳定复现：

```bash
make setup
make eval
make eval-ablation
make test
npm --prefix frontend run build
```

评测数据位于 `eval/data/`，包含 `seed`、`eval_dev`、`eval_test` 三路数据，开发集和测试集合计 50 个展开后的 NL2SQL case。runner 输出首轮准确率、纠错后准确率、恢复率、尝试次数、P50/P95 延迟和估算 token/cost；`data/local/eval/` 只保存本地结果，不应提交。

前端保留原有查询、澄清、Markdown、ECharts 和表格导出能力，并新增结构化执行轨迹与离线任务 SSE 面板。SSE 面板验证 `POST /api/tasks`、`GET /api/tasks/{task_id}/events`、断线重放和终态查询，不要求登录态或真实外部服务。

> 注意：阶段 0 的 SSE 面板只验证可恢复的假任务事件；它不会触发岗位采集、真实 LLM 或 CareerAgent。
>
> 普通 `/api/query` 仍是同步接口，若要把采集、分析和通知改造成生产级异步队列，需要后续接入持久化队列、worker 和任务权限。

### 数据、隐私与免责声明

- `data/fixtures/jobs_seed.json` 是人工编写的合成岗位数据，不代表真实招聘市场，也不构成薪资或求职承诺。
- 真实岗位数据、完整 JD、简历原文、Runtime Store 和评测结果均默认是本地敏感数据，已通过 Git 忽略规则排除。
- 简历在进入 CareerAgent 前先做本地解析和 PII 脱敏；系统不会自动投递、发送消息、绕过验证码或风控。
- 所有岗位结论应结合样本量、数据时间和原始证据复核，模型生成的自然语言不能替代用户判断。

### 简历 Highlight Bullets

- 设计并实现基于 Pydantic 契约的 Multi-Agent 求职决策系统，打通岗位 ETL、全量 Schema 注入、NL2SQL SQL Self-Correction 与证据化 CareerReport。
- 构建离线可复现评测 runner，覆盖 50 个 NL2SQL case，量化首轮/最终准确率、纠错恢复率、SQL 尝试次数、P50/P95 延迟及估算 token 成本，并支持纠错/few-shot 消融。
- 将 Vue 查询界面拆出澄清面板、执行轨迹和 SSE 离线演示组件，支持断线恢复、Markdown 报告、ECharts 可视化和表格导出；演示不依赖 live 爬取与登录。

## 常见问题

- 为什么离线命令不需要模型 Key？`make setup`、`make eval`、阶段 0 SSE 和合成 ETL 使用确定性逻辑；只有真实自然语言问数和可选的 CareerAgent LLM 建议层需要 `LLM_API_KEY`。
- 为什么没有岗位结果？先运行 `make setup` 或 `uv run python backend/etl.py --jobs data/fixtures/jobs_seed.json`，然后重启后端，让它加载新的 `jobs_market` 数据版本。
- 为什么前端页面打不开或没有数据？确认后端已运行在 8000 端口，再在 5173 端口启动前端；API 地址由 `frontend/src/api.ts` 的开发配置决定。
- BOSS 直聘采集是否开箱即用？不是。它需要 Chrome CDP、用户登录态和人工确认的采集环境；项目不绕过验证码或风控，建议先用合成数据验证主链路。
- 仓库中的岗位和评测数据是否代表真实市场？不是。公开样本是合成或脱敏数据，真实岗位、JD 和简历应只保存在本地，并在使用结论前复核数据时间、样本量和原始证据。
