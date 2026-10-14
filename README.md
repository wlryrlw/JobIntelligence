# JobIntelligence

JobIntelligence（JobIntel） 是一个面向个人求职决策的本地 Agent。本项目把 BOSS 直聘岗位采集、自然语言数据查询、岗位 JD 检索、简历匹配分析串成一条工作流，帮助用户从“我应该关注哪些岗位、市场通常要求什么、我的简历还缺什么”这些问题出发，得到带有数据证据的分析结果。



## 功能

- **岗位市场查询**：用自然语言查询岗位数量、薪资、城市、经验和学历等市场信息。
- **岗位数据搜集**：支持从 BOSS 直聘采集岗位列表和职位描述，并更新本地岗位数据。
- **岗位要求分析**：总结目标岗位常见的技能、经验和学历要求，帮助用户了解市场需求。
- **简历匹配**：上传文本或文本型 PDF 简历，查看与目标岗位的技能匹配情况和能力缺口。
- **结果展示**：在网页中查看查询结果、分析报告、图表和任务进度，并保留可追溯的岗位证据。



## 环境要求

- Python **3.11**
- [uv](https://docs.astral.sh/uv/)：用于管理 Python 依赖和运行命令。
- Node.js **`^20.19.0` 或 `>=22.12.0`**：运行 Vue/Vite 前端。
- npm **`>=10`**：安装前端依赖和执行前端脚本。
- Docker Compose v2：仅在启用本地 Milvus JD RAG 时需要。
- Chrome：仅在使用 BOSS 直聘 CDP 采集时需要，并且必须由用户本人完成登录。


## 安装与运行

### 环境配置
在 `backend/.env` 中设置 LLM api_key（必须）、embedding_api_key（可选）和 judge_api_key（可选）。

本地运行RAG可以启动仓库提供的本地 Milvus 配置（可选）：

```bash
docker compose -f docker-compose.milvus.yml up -d
```

### 使用 uv 进行安装与运行
安装 Python 依赖并生成本地岗位数据：

```bash
uv sync
uv run python backend/etl.py --jobs data/fixtures/jobs_seed.json
```

启动后端：
后端默认监听 `http://127.0.0.1:8000`。FastAPI 文档位于 <http://127.0.0.1:8000/docs>，健康检查接口为 <http://127.0.0.1:8000/api/health>。

```bash
uv run python backend/run.py
```

启动前端：
前端默认监听 <http://127.0.0.1:5173>，Vite 会将 `/api` 代理到 `http://127.0.0.1:8000`。如果后端地址不同，可在 `frontend/.env` 中设置 `VITE_API_BASE`。另开一个终端，在仓库根目录执行：

```bash
npm ci --prefix frontend
npm --prefix frontend run dev
```

### 使用 make 命令快速运行

```bash
make setup
make backend
make frontend
```

本地演示账号由后端内置，仅用于开发验证：

| 用户名 | 密码 | 用途 |
| --- | --- | --- |
| `candidate` | `candidate123` | 求职分析工作区 |
| `admin` | `admin123` | 管理员/完整本地数据权限 |



## 项目结构

```text
JobIntel/
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI 路由、查询/采集/SSE 接口
│   │   ├── career/           # 简历解析、脱敏、岗位画像与 CareerAgent
│   │   ├── etl/              # 岗位数据规范化、薪资解析、Schema 与发布
│   │   ├── querying/         # Schema Catalog、QueryAgent、SQL 校验和 DuckDB
│   │   ├── rag/              # JD RAG 客户端、索引与检索服务
│   │   ├── workflows/        # LangGraph 查询工作流与结果构建
│   │   ├── mcp_runtime/      # 只读数据库 MCP 工具运行时
│   │   ├── services/         # 会话、记忆、归档和应用服务
│   │   ├── security/         # 本地认证与数据库/表权限范围
│   │   ├── coordinator.py    # 任务路由和资源协调
│   │   ├── crawling.py       # 采集任务生命周期与后台执行边界
│   │   └── main.py           # FastAPI 应用
│   ├── etl.py                # JSON → CSV / Schema / 数据质量报告
│   ├── run.py                # Uvicorn 启动入口
│   └── tests/                # 后端、ETL、查询、RAG、SSE 和权限测试
├── frontend/
│   ├── src/                  # Vue 页面、组件、API 客户端和类型
│   ├── package.json          # Vite 开发/构建脚本
│   └── vite.config.ts        # 开发服务器及 /api 代理
├── integrations/
│   └── boss_zhipin/          # 独立的 BOSS 直聘 Chrome CDP 采集集成
├── data/
│   ├── fixtures/             # 可提交的合成岗位样本
│   └── README.md             # 数据提交、运行时数据和隐私边界
├── eval/
│   ├── data/                 # NL2SQL 和 RAG 评测数据
│   ├── rag/                  # 岗位 JD RAG 评测 runner
│   └── README.md             # 离线评测说明
├── pyproject.toml            # Python 依赖、pytest 和项目配置
├── uv.lock                   # Python 依赖锁文件
├── Makefile                  # 常用开发、测试和评测命令
└── LICENSE                   # MIT License
```



## 评测

```bash
make test              # 运行后端和 BOSS 集成的自动化测试

make eval-smoke        # 运行 6 个固定样例的 NL2SQL 冒烟评测
make eval              # 运行 eval_test 全量 NL2SQL 评测
make eval-ablation     # 运行 NL2SQL 纠错和 few-shot 消融评测

make eval-judge-smoke  # 对 seed 数据运行 LLM-as-judge 冒烟评测
make eval-judge        # 对 eval_test 运行 LLM-as-judge 参考答案评测

make rag-coverage      # 校验 RAG 评测集与当前岗位数据的覆盖情况
make rag-retrieval     # 运行 JD RAG 的 BM25、Embedding 和混合检索评测
make rag-answers       # 生成岗位 JD RAG 的回答评测样本

make rag-judge         # 对 RAG 回答执行独立 Judge 评测
make rag-eval          # 依次运行 RAG 覆盖、检索、回答和 Judge 全流程
```
更多评测内容查看 [`eval/README.md`](eval/README.md)。


## 数据、隐私与安全边界

- 只把可复现的合成 fixture 和必要的公开说明放入 `data/`；真实岗位、Cookie、登录态、Chrome profile、运行时任务状态和真实简历应保存在被 Git 忽略的本地目录。
- 简历上传支持 `.txt`、Markdown 和文本型 PDF；系统会在进入后续 Agent 前提取文本并对确定性 PII 做脱敏。简历原文件和抽取结果默认短期本地保存，使用完毕后应通过界面/API 删除。
- QueryAgent 生成的 SQL 不是安全证明。后端仍会执行单语句、只读、表白名单、字段约束和禁止外部访问等校验，并按用户角色限制数据库和表范围。
- SSE 只发送任务阶段、状态和必要的结构化结果，不发送模型隐藏思维链、完整简历文本或访问凭证。
- BOSS 直聘采集仅用于个人求职分析；遇到登录失效、验证码或风控页面时应由用户人工处理。


## 许可证

本项目采用 [MIT License](LICENSE)。第三方服务、模型、招聘网站和数据源仍受其各自的服务条款、许可证和使用限制约束。
