# OfferAgent: 基于 Multi-Agent 协作与 NL2SQL 的智能求职决策系统

OfferAgent 是一个基于 Multi-Agent 架构与 NL2SQL 的智能求职决策系统：自动采集 BOSS直聘目标岗位数据（CDP 被动捕获）→ ETL 数值化入库（DuckDB）→ 自然语言问数（NL2SQL + SQL 自修正）→ 个性化求职决策报告（技能缺口分析 / 简历改写建议 / 面试清单），全链路通过 SSE 流式返回结构化执行轨迹、SQL、图表与报告。

项目由两个已有项目融合优化而成：

- [boss-zhipin-scraper](boss-zhipin-scraper/README.md)：CDP 被动捕获式 BOSS直聘采集工具（核心逻辑集中在单文件，包含完整 mock 测试），输出 JSON/CSV 明文薪资数据
- [AskData](AskData/README.md)：FastAPI + LangGraph 的 NL2SQL 系统（DuckDB 只读引擎 + sqlglot 安全校验 + interrupt 人机澄清，12 个测试文件）

> 文档约定：本文档严格区分「继承」（源项目已实现，直接复用）、「新建」（本项目新开发）与「不采用」（显式砍掉并说明理由）。Multi-Agent 不是营销标签：每个 Agent 都必须有明确的输入/输出契约、状态边界、工具权限和失败协议。开发周期预算 3–4 周。

## 项目核心点（简历视角）

1. **闭环式 Multi-Agent 工作流**：基于 LangGraph 编排 Coordinator、MarketDataAgent、QueryAgent 和 CareerAgent，完成「岗位数据采集 → ETL 数值化入库 → 自然语言问数 → 求职决策报告」全链条；Coordinator 负责意图路由、任务状态、新鲜度检查与 interrupt 人机澄清。
2. **真实反爬环境下的采集工具化**：基于 CDP 被动捕获（旁听页面自身请求，零注入）获取明文薪资，继承风控码分类（code 31/37）与失败上报机制，封装为带状态上报的采集工具。
3. **NL2SQL + SQL Self-Correction**：sqlglot 只读校验（禁写操作 / 禁 `SELECT *`），可修复的 SQL 执行错误回喂 QueryAgent 自动重试修复（最多 2 次），权限错误和安全校验错误不交给模型绕过。
4. **评测驱动（Eval-Driven）**：内置开发集与独立测试集，至少 50 题中文 NL2SQL 评测基准，报告首轮准确率、纠错后准确率、纠错恢复率、延迟和消融实验结果——这是区别于"跑通了 demo"的 Agent 工程成熟度证明。
5. **可解释的求职决策**：JD 技能抽取（LLM 结构化输出，与词频基线对比）× 简历技能，输出逐项给证据的技能覆盖/缺口分析，而非黑盒匹配分数。

## 技术决策（已锁定）

| 决策项 | 选择 | 理由 |
|---|---|---|
| 持久层 | **DuckDB（CSV 视图，只读）** | 直接复用 AskData 的执行器与 SQL 安全层，ETL 产出 CSV 即可，零引擎改造；列存对分析型查询（P50/P75、词频聚合）友好 |
| Schema 供给方式 | **全量表结构注入 Prompt，不做 Schema RAG** | 岗位库仅 3 张表约 30 个字段，全量注入远小于上下文窗口；省去 embedding/rerank API 成本与检索延迟。面试口径："schema 仅 30 字段，检索无收益，是可量化的取舍" |
| Agent 协作协议 | **Pydantic 输入/输出契约 + LangGraph 状态机** | 保留 Multi-Agent 架构，但禁止以无约束 `dict` 传递核心状态；每个 Agent 明确职责、工具白名单、状态转换和失败类型 |
| 前后端通信 | **SSE 流式事件** | Agent 过程可见性使用结构化执行轨迹（意图/步骤/SQL/纠错/报告），不暴露模型隐藏思维链；兜底方案为 REST + 轮询 |
| 运行时任务状态 | **本地文件 JSON + append-only JSONL 事件日志** | 分析数据仍使用 DuckDB CSV 只读视图；任务状态、SSE 事件和采集任务状态需要跨请求恢复，暂不引入 PostgreSQL/SQLite 业务持久层 |
| 爬虫触发方式 | **独立采集任务 API/CLI + Coordinator 引导** | 抓取一次耗时数分钟，作为同步 LangGraph 节点会阻塞整个 graph；Coordinator 只创建/查询采集任务，不直接在请求线程运行 scraper |
| 人岗匹配方法 | **可解释技能覆盖分析** | 逐项给出"已覆盖（简历证据 + JD 证据）/ 部分覆盖 / 缺口 / 建议"，面试可答辩；不输出不可解释的单一匹配分数 |
| JD 技能抽取 | **词频 regex（继承）+ LLM structured output（新建），双方案对比** | 对比结果本身是评测素材 |
| Python 版本 | **统一 3.11** | AskData 锁 3.11（langgraph/duckdb/mcp 版本约束）；scraper 要求 ≥3.10，无冲突 |
| 前端图表 | **业务级 ChartSpec → ECharts** | Agent 只输出受限的 Pydantic ChartSpec，后端/前端确定性转换为 ECharts option，不允许模型直接生成任意 option |

## 与源项目能力对照

| 能力 | 源项目现状 | OfferAgent 中的处理 |
|---|---|---|
| CDP 被动采集 / 登录态管理 / 城市码表 / 增量原子写 | scraper 已实现 | **继承**，封装为采集入口工具 |
| 风控码分类（31/37）与 fail-fast 停止 | scraper 已实现 | **继承**，错误改为状态上报给 Coordinator |
| 薪资数值化（min/max/薪月/年薪折算） | 两项目均无（salary 为原始字符串 `"40-70K·13薪"`） | **新建**，阶段 1 硬前置 |
| 持久化存储 | scraper 无 DB；AskData 为 DuckDB-over-CSV | **继承** DuckDB 引擎，**新建** ETL 产出 CSV |
| `_schema.json` 生成与注册 | AskData 静态硬编码（`database.py` 写死 ecommerce_ops） | **新建**：改造生成脚本 + loader 接入 |
| Schema RAG（embedding + rerank 检索） | AskData 已实现（BM25+稠密+RRF+rerank） | **不采用**（见技术决策；连带移除 embedding/rerank API 依赖） |
| 意图路由 / interrupt 人机澄清 | AskData 已实现 | **继承**，新增 `career_advice` 意图，并补齐简历、岗位参数和采集状态的澄清分支 |
| NL2SQL 生成 + 只读安全校验 | AskData 已实现 | **继承** |
| SQL Self-Correction 纠错环 | AskData 刻意 fail-fast（有守卫测试断言不重试） | **新建**：错误回喂重试，需同步修改守卫测试 |
| 图表配置生成 | AskData 仅 bar/pie 配置，前端 CSS 渲染 | **扩展**：line/直方图 → ECharts 配置 |
| SSE 流式 / 执行轨迹展示 | 均无（阻塞式 REST + 静态 spinner） | **新建**；阶段 0 先完成最小事件协议与断线恢复 Spike |
| JD 技能抽取 | scraper regex 词频已实现 | **继承**为基线，**新建** LLM 结构化抽取对比 |
| 简历解析 / 技能缺口分析 | scraper 曾移除 resume 打分（守卫测试禁止 LLM 依赖） | **新建**：可解释覆盖分析（新仓库不受原守卫测试约束） |
| 评测基准 | 均无 | **新建**（50+ 题） |
| 测试体系 | scraper 和 AskData 均已有较完整测试 | **继承**测试纪律，重点补 Agent 契约、薪资解析/ETL/纠错环/路由/SSE/数据质量测试 |

## Multi-Agent 契约与状态边界

本项目保留 Multi-Agent 架构，但各 Agent 不共享任意可变上下文，也不直接修改其他 Agent 的内部状态。Agent 之间只通过经过 Pydantic 校验的输入/输出对象和 LangGraph 状态传递结果。采集侧的 CDP scraper 是 MarketDataAgent 使用的工具/worker，不把浏览器自动化逻辑伪装成 LLM Agent。

### Agent 职责与输入/输出契约

| Agent | 职责 | 输入契约 | 输出契约 | 工具/权限边界 |
|---|---|---|---|---|
| `Coordinator` | 识别意图、解析任务参数、检查资源与数据新鲜度、决定下一节点、处理 interrupt | `UserRequest`、`SessionContext`、`TaskSnapshot` | `RouteDecision`、`ClarificationRequest`、`TaskTransition` | 不直接执行 scraper 或 SQL；只能创建/查询下游任务 |
| `MarketDataAgent` | 创建采集任务、驱动 CDP scraper、处理 ETL、发布数据集版本 | `CrawlRequest(keyword, city, pages, detail)` | `CrawlRunResult(run_id, status, dataset_version, counts, failure)` | 只能使用受控 scraper worker；遇到风控、登录失效或验证页立即停止并上报 |
| `QueryAgent` | 根据完整 Schema 生成、校验、执行和修复 SQL，返回数据结果 | `QueryPlan`、`SchemaCatalog`、`DatasetVersion`、`SqlAttemptHistory` | `QueryResult`、`SqlAttempt[]`、`ChartSpec[]` | 只能调用只读数据库工具；最多 3 次 SQL 执行尝试，其中最多 2 次为修复重试 |
| `CareerAgent` | 提取 JD/简历技能、构建岗位画像、做证据匹配并拼装求职报告 | `ResumeEvidence`、`JobProfile`、`SkillCatalog` | `CareerReport`、`SkillCoverageItem[]`、`ReportEvidence[]` | 不得创造简历事实；建议和已验证事实分离输出 |

### 核心契约对象

以下对象为实现阶段的最小契约，字段可继续细化，但不能退化为核心链路中的无约束 `dict`：

```text
UserRequest
  task_id: str
  session_id: str
  text: str
  resume_id: str | None
  target: TargetJobRequest | None

TargetJobRequest
  keyword: str
  city: str | None
  company: str | None
  experience: str | None
  degree: str | None

CrawlRunResult
  run_id: str
  status: queued | running | blocked | succeeded | failed | cancelled
  dataset_version: str | None
  job_count: int
  detail_count: int
  failure: FailureInfo | None

SqlAttempt
  attempt: int
  sql: str
  validation: passed | rejected
  execution: succeeded | failed
  error_kind: str | None
  retryable: bool

SkillCoverageItem
  skill: str
  category: str
  status: covered | partial | missing
  jd_evidence: list[EvidenceRef]
  resume_evidence: list[EvidenceRef]
  suggestion: str | None
```

### 任务状态与持久化边界

用户请求和采集任务均使用显式状态机：

```text
created → running → waiting_clarification → running
                         ↓
              completed / failed / cancelled
```

采集任务额外支持：

```text
queued → running → blocked_by_login
                 → blocked_by_risk_control
                 → etl_running → succeeded / failed
```

运行时至少保存以下标识：`task_id`、`crawl_run_id`、`dataset_version`、`resume_id`、`event_id`。分析数据继续使用 DuckDB CSV 只读视图；任务快照使用本地 JSON，事件使用 append-only JSONL，以支持前端断线恢复和进程重启后的结果查询。运行时文件不得写入公开仓库。

## 核心架构

```mermaid
flowchart TB

    %% =========================================================
    %% 1. 用户与接入层
    %% =========================================================

    User((用户))

    subgraph UX["用户与接入层"]
        FE["Vue 3 前端<br/>任务创建 · 简历上传 · SSE 执行轨迹 · ECharts"]
        API["FastAPI Task API<br/>创建任务 · 查询状态 · 提交澄清"]
    end

    User -->|"自然语言问题 / 简历 / 目标岗位"| FE
    FE -->|"HTTP 请求"| API
    API -->|"创建或恢复 task_id"| Coordinator
    API -->|"SSE 事件流"| FE

    %% =========================================================
    %% 2. 编排层
    %% =========================================================

    subgraph Orchestrator["编排层"]
        Coordinator["Coordinator Agent<br/>意图路由 · 任务状态 · 资源检查<br/>数据新鲜度 · interrupt 澄清 · Agent 协调"]
        Contracts["Typed State + Pydantic Contracts<br/>UserRequest · TaskSnapshot · AgentResult<br/>FailureInfo · SseEvent"]
        ResourceGate{"资源与数据状态检查<br/>简历 · 目标岗位 · 数据集版本"}

        Coordinator --> Contracts
        Contracts --> ResourceGate
    end

    %% =========================================================
    %% 3. MarketDataAgent
    %% =========================================================

    subgraph MarketData["Agent 1 · MarketDataAgent"]
        direction TB

        CrawlRequest["CrawlRequest<br/>keyword · city · pages · detail"]
        CrawlTask["Crawl Task Manager<br/>任务排队 · Chrome Profile 锁<br/>取消 · 超时 · 状态持久化"]
        CDPWorker["CDP Scraper Worker<br/>监听页面自身网络响应<br/>明文薪资 · 风控分类"]
        RawCapture["Raw Capture<br/>原始 JSON / CSV"]
        ETL["ETL Pipeline<br/>字段映射 · 薪资解析 · 去重<br/>技能归一化 · 数据质量检查"]
        CrawlResult["CrawlRunResult<br/>run_id · status · counts<br/>failure_code · dataset_version"]

        CrawlRequest --> CrawlTask
        CrawlTask --> CDPWorker
        CDPWorker --> RawCapture
        RawCapture --> ETL
        ETL --> CrawlResult
    end

    %% =========================================================
    %% 4. QueryAgent
    %% =========================================================

    subgraph Query["Agent 2 · QueryAgent"]
        direction TB

        QueryPlan["QueryPlan<br/>standalone_query · filters · metrics"]
        SchemaCatalog["SchemaCatalog<br/>物理表名 · 字段类型 · 中文含义<br/>别名 · 聚合能力 · 数据集版本"]
        SQLGenerator["SQL Generator<br/>生成结构化 SQLAttempt"]
        SQLGuard["SQL Safety Guard<br/>sqlglot · 单语句 · 只读<br/>表白名单 · 禁 SELECT *"]
        SQLExecutor["DuckDB Read-only Executor<br/>CSV View · 行数上限"]
        ExecuteGate{"执行结果"}
        SQLRepair["SQL Repair<br/>错误分类 · 有界错误回喂<br/>最多 2 次修复"]
        QueryResult["QueryAgentResult<br/>QueryResult · SqlAttempt[]<br/>JobProfile · ChartSpec[]"]

        QueryPlan --> SQLGenerator
        SchemaCatalog --> SQLGenerator
        SQLGenerator --> SQLGuard
        SQLGuard --> SQLExecutor
        SQLExecutor --> ExecuteGate

        ExecuteGate -->|"成功"| QueryResult
        ExecuteGate -->|"可修复错误<br/>且 attempt < 3"| SQLRepair
        SQLRepair --> SQLGenerator
        ExecuteGate -->|"安全错误 / 权限错误<br/>不可修复 / 已耗尽"| QueryResult
    end

    %% =========================================================
    %% 5. CareerAgent
    %% =========================================================

    subgraph Career["Agent 3 · CareerAgent"]
        direction TB

        CareerInput["CareerRequest<br/>resume_id · JobProfile<br/>JD Evidence · 用户目标"]

        ResumeParser["Resume Parser<br/>文本 / 文本型 PDF"]
        PIIRedaction["PII Redaction<br/>姓名 · 电话 · 邮箱 · 地址"]
        ResumeExtractor["Resume Evidence Extractor<br/>技能 · 项目经历 · 原文证据<br/>严格 Pydantic 输出"]

        JDSkillExtractor["JD Skill Extractor<br/>regex baseline + LLM structured output<br/>skill canonical · category · evidence"]

        EvidenceMatcher["Evidence Matcher<br/>别名归一化 · must-have/preferred<br/>covered / partial / missing"]

        ReportAssembler["Report Assembler<br/>事实层模板化<br/>建议层 LLM 生成<br/>STAR 改写禁止虚构"]

        CareerReport["CareerReport<br/>技能覆盖 · 缺口建议<br/>简历改写方向 · 面试清单"]

        CareerInput --> ResumeParser
        ResumeParser --> PIIRedaction
        PIIRedaction --> ResumeExtractor

        CareerInput --> JDSkillExtractor
        ResumeExtractor --> EvidenceMatcher
        JDSkillExtractor --> EvidenceMatcher
        CareerInput --> EvidenceMatcher
        EvidenceMatcher --> ReportAssembler
        ReportAssembler --> CareerReport
    end

    %% =========================================================
    %% 6. 数据与运行时存储层
    %% =========================================================

    subgraph Data["数据层"]
        RawData[(Raw JSON / CSV<br/>本地可重放输入)]
        DuckDB[(DuckDB CSV Views<br/>jobs · job_skills · job_details)]
        RuntimeStore[(Runtime Store<br/>Task JSON · Event JSONL)]
        ResumeStore[(Resume Evidence<br/>短期本地保存)]
    end

    RawCapture --> RawData
    RawData --> ETL
    ETL --> DuckDB

    ResumeExtractor --> ResumeStore

    %% =========================================================
    %% 7. Agent 协作关系
    %% =========================================================

    ResourceGate -->|"无数据 / 数据过期"| CrawlRequest
    ResourceGate -->|"database_query"| QueryPlan
    ResourceGate -->|"career_advice 且数据可用"| QueryPlan

    CrawlResult -->|"CrawlRunResult<br/>成功 / 阻塞 / 失败"| Coordinator
    CrawlResult -->|"成功发布 dataset_version"| QueryPlan

    DuckDB -->|"SchemaCatalog"| SchemaCatalog
    DuckDB -->|"只读执行"| SQLExecutor

    QueryResult -->|"QueryResult + JobProfile<br/>+ JD Evidence"| Coordinator
    Coordinator -->|"ResumeEvidence + JobProfile"| CareerInput
    CareerReport -->|"CareerReport"| Coordinator

    %% =========================================================
    %% 8. 模型网关
    %% =========================================================

    subgraph ModelLayer["模型调用层"]
        ModelGateway["Model Gateway<br/>异步调用 · Retry/Backoff<br/>响应解析 · Pydantic 校验"]
        LLM["外部 LLM Provider"]
        ModelGateway --> LLM
    end

    Coordinator -.->|"RouteDecision"| ModelGateway
    SQLGenerator -.->|"SQL structured output"| ModelGateway
    SQLRepair -.->|"Repair structured output"| ModelGateway
    JDSkillExtractor -.->|"Skill structured output"| ModelGateway
    ResumeExtractor -.->|"脱敏文本"| ModelGateway
    ReportAssembler -.->|"仅建议层"| ModelGateway

    %% =========================================================
    %% 9. 状态与 SSE 事件
    %% =========================================================

    Coordinator <-->|"任务快照 / 状态恢复"| RuntimeStore
    Coordinator -->|"结构化 Agent 事件<br/>step · SQL · retry · result"| RuntimeStore
    API <-->|"读取任务状态 / 事件"| RuntimeStore

    %% =========================================================
    %% 10. 评测
    %% =========================================================

    Eval["Eval Runner<br/>seed / eval_dev / eval_test<br/>首轮准确率 · 最终准确率<br/>纠错恢复率 · P95 延迟 · F1"]

    Eval -.->|"QueryAgent 回归"| SQLGenerator
    Eval -.->|"Self-Correction 消融"| SQLRepair
    Eval -.->|"JD 技能抽取评测"| JDSkillExtractor
    Eval -.->|"覆盖分析评测"| EvidenceMatcher

    %% =========================================================
    %% 11. 样式
    %% =========================================================

    classDef user fill:#ffffff,stroke:#334155,stroke-width:2px,color:#0f172a;
    classDef frontend fill:#e0f2fe,stroke:#0284c7,stroke-width:2px,color:#0c4a6e;
    classDef api fill:#f0f9ff,stroke:#0284c7,stroke-width:2px,color:#0c4a6e;
    classDef coordinator fill:#fef3c7,stroke:#d97706,stroke-width:3px,color:#78350f;
    classDef contract fill:#fff7ed,stroke:#ea580c,stroke-width:2px,color:#7c2d12;
    classDef agent fill:#f3e8ff,stroke:#9333ea,stroke-width:1.5px,color:#581c87;
    classDef decision fill:#fff7ed,stroke:#ea580c,stroke-width:2px,color:#7c2d12;
    classDef data fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#14532d;
    classDef runtime fill:#f1f5f9,stroke:#64748b,stroke-width:2px,color:#1e293b;
    classDef model fill:#fce7f3,stroke:#db2777,stroke-width:2px,color:#831843;
    classDef eval fill:#f5f3ff,stroke:#8b5cf6,stroke-width:2px,color:#4c1d95;

    class User user;
    class FE frontend;
    class API api;
    class Coordinator coordinator;
    class Contracts contract;
    class ResourceGate,ExecuteGate decision;

    class CrawlRequest,CrawlTask,CDPWorker,RawCapture,ETL,CrawlResult agent;
    class QueryPlan,SchemaCatalog,SQLGenerator,SQLGuard,SQLExecutor,SQLRepair,QueryResult agent;
    class CareerInput,ResumeParser,PIIRedaction,ResumeExtractor,JDSkillExtractor,EvidenceMatcher,ReportAssembler,CareerReport agent;

    class RawData,DuckDB,ResumeStore data;
    class RuntimeStore runtime;
    class ModelGateway,LLM model;
    class Eval eval;

    style UX fill:#eff6ff,stroke:#0284c7,stroke-width:2px;
    style Orchestrator fill:#fffbeb,stroke:#d97706,stroke-width:2px;
    style MarketData fill:#f0fdf4,stroke:#16a34a,stroke-width:2px;
    style Query fill:#f5f3ff,stroke:#8b5cf6,stroke-width:2px;
    style Career fill:#fdf2f8,stroke:#db2777,stroke-width:2px;
    style Data fill:#f0fdf4,stroke:#16a34a,stroke-width:2px;
    style ModelLayer fill:#fdf2f8,stroke:#db2777,stroke-width:2px;
```

## 模块详细设计

### Coordinator（编排器）

**意图路由**（继承 AskData 的意图分类，扩展一项）：

| 意图 | 行为 | 来源 |
|---|---|---|
| `direct_response` | 闲聊 / 项目说明，直接回答 | 继承 |
| `data_qa` | 基于历史查询结果做综合分析（报告 + 图表） | 继承 |
| `database_query` | NL2SQL 问数 | 继承 |
| `career_advice` | 求职材料生成（需要简历 + 目标岗位画像） | **新建** |

**任务资源检查与数据新鲜度检查**（新建，`database_query` 与 `career_advice` 的前置节点）：

- 先将用户问题解析成 `TargetJobRequest`：至少明确岗位关键词，城市缺失时由用户选择或使用文档中明确的默认值；不根据模糊文本静默猜测过滤条件。
- 查询 `crawl_runs` / 数据集 manifest，按 `keyword + city + dataset_version` 判断数据是否可用；不能只依赖 `jobs` 的 `MAX(scraped_at)`，因为一次采集可能部分成功或被风控中止。
- 无数据或数据超过 7 天 → 创建/展示采集任务建议，**不在当前请求线程内同步爬取**；如果用户明确点击采集，则由 `MarketDataAgent` 创建独立任务。
- `career_advice` 还必须检查 `resume_id`；缺少简历、目标岗位参数或有效数据集时进入 interrupt/澄清状态。
- 闭环演示路径："提问 → 检测到库空 → 创建采集任务 → 展示采集状态 → ETL 完成 → 重新提问/继续原任务"。

**SSE 事件汇流**：将各 Agent 节点的启动/结束、采集状态、生成的 SQL、纠错重试、图表配置、报告分片汇聚为统一事件流下发前端。对用户展示的是结构化执行轨迹，不是模型隐藏思维链。每个事件包含 `event_id`、`task_id`、递增 `sequence`、事件类型、时间戳和经过 Pydantic 校验的 payload；支持心跳、断线重连、重复事件去重和任务完成后的结果查询。

**最小 API 契约**：

| API | 作用 |
|---|---|
| `POST /api/tasks` | 创建查询/求职分析任务，返回 `task_id` |
| `GET /api/tasks/{task_id}/events` | 通过 SSE 订阅结构化执行轨迹 |
| `GET /api/tasks/{task_id}` | 查询当前任务快照和最终结果 |
| `POST /api/tasks/{task_id}/clarify` | 提交 interrupt 澄清选项并恢复任务 |
| `POST /api/crawl-runs` | 创建独立岗位采集任务，返回 `crawl_run_id` |
| `GET /api/crawl-runs/{run_id}` | 查询采集/ETL 状态和数据集版本 |
| `POST /api/resumes` | 上传并解析简历，返回 `resume_id`，不返回原始文件内容 |
| `DELETE /api/resumes/{resume_id}` | 删除简历文件和抽取结果 |

如果 SSE 在部署环境不可用，保留相同任务模型和事件模型，前端改用 `GET /api/tasks/{task_id}` 轮询；这只是传输层兜底，不应重新实现一套独立的工作流。

### ① 数据采集 Agent

- **Agent 定位**：`MarketDataAgent` 负责任务创建、采集状态、ETL 编排和数据集版本；CDP scraper 是其受控的工具/worker，不在 FastAPI 请求线程中直接执行。
- **继承**（scraper 已实现，不重写）：CDP 被动捕获页面自身网络响应、明文薪资、隔离 Chrome profile 登录态、城市码表三级解析、增量原子写、`job_id`（md5 of job_link）去重、风控码分类（`{31, 37}` + 消息关键词兜底）、请求预算（500 次/会话）与随机间隔。
- **新建**：
  - 封装为采集入口（CLI / 任务 API）可调用的 worker；优先使用独立子进程，stdout 通过 NDJSON 传输结构化事件，避免 scraper 的阻塞调用阻塞 FastAPI 事件循环。
  - 采集过程输出结构化状态事件（排队 / 登录等待 / 进度 / 风控 / 详情失败 / ETL / 完成）供 `Runtime Store` 和前端展示。
  - 使用 `crawl_run_id` 记录一次完整采集任务；只有采集和 ETL 成功后才发布新的 `dataset_version`。
  - 对同一隔离 Chrome profile 加采集锁，禁止多个采集任务并行争抢 CDP 端口或登录态。
  - ETL 数值化管道（见下）
- **失败协议**：将登录失效、风控验证、网络超时、详情页截断、ETL 失败分别映射为稳定的 `error_code` 和用户可读消息；不把原始异常字符串直接当作状态枚举。
- **边界**：不做同步"自愈"决策树。遇到风控 → 停止 + 上报 + 提示人工处理（合规红线，继承 scraper 原则）；上层 Agent 的职责是**决策如何响应采集失败状态**（换关键词 / 换城市 / 提示用户），而非绕过风控。

### ② 问数 Agent（NL2SQL）

- **Agent 定位**：`QueryAgent` 负责从自然语言到 SQL 结果的完整闭环；Coordinator 只提供任务上下文和允许访问的数据集，不修改 QueryAgent 的 SQL 内容。
- **继承**：意图预处理与 standalone 改写、interrupt 人机澄清（LangGraph `interrupt()` / `Command(resume=...)`）、DuckDB 只读引擎（sqlglot 校验：单语句、仅 SELECT/WITH、禁写操作、禁 `SELECT *`、表白名单、200 行上限）。
- **移除**：Schema 检索节点、`schema_store.json` 索引构建、embedding / rerank API 调用（连带删除相关 config 与 admin 重建接口）。
- **新建**：
  - 全量 Schema 注入前先生成确定性的 `SchemaCatalog`，包含物理表名、字段类型、中文业务含义、别名、可用聚合、样例值和数据集版本；使用实际 token 统计验证“无 Schema RAG”的取舍。
  - **SQL Self-Correction**：执行失败时仅对可修复错误将错误类型 + 有界错误信息 + 失败 SQL 回喂 QueryAgent 重新生成，最多重试 2 次；仍失败则如实返回错误信息（fail-fast 作为纠错耗尽后的最终行为）。每次尝试都记录 `SqlAttempt`，并区分首轮失败、修复成功和修复耗尽。
  - 错误分类：语法错误、未知字段、未知表、类型绑定错误可以重试；权限错误、禁止 SQL、多语句、`SELECT *`、数据库不存在、超时、空结果和模型服务不可用不交给模型绕过。
  - **图表工具扩展**：在现有 bar/pie 基础上增加 line / 直方图，输出受限的 Pydantic `ChartSpec`，再由确定性映射生成 ECharts 配置；不得让模型直接生成任意 ECharts option。

### ③ 求职决策 Agent

- **Agent 定位**：`CareerAgent` 只处理岗位画像、简历证据和求职建议；不负责抓取网页、不直接编写 SQL、不自动投递岗位。
- **JD 技能抽取**：LLM structured output（Pydantic：技能名 / 规范技能名 / 类别 / JD 原文证据 / 置信度），与继承的 regex 词频方案双路入库（`source`、`extractor_version` 字段区分）。regex 是可复现基线，LLM 是增强方案；“一致率”只作为 agreement 指标，准确性使用人工标注集上的 precision / recall / F1。
- **简历解析**（新建）：支持文本型 PDF / 文本上传（pypdf）；扫描版 PDF 无有效文本时明确失败，不静默生成分析。上传限制文件类型、大小和页数，抽取结果只保留必要的结构化证据。
- **隐私边界**：原始简历默认只在本地短期保存；发送给外部 LLM 前脱敏姓名、电话、邮箱、地址等 PII；日志、SSE 和任务快照不得包含完整简历文本。提供删除 `resume_id` 的接口，并在 README 中说明模型服务商的数据处理风险。
- **覆盖 / 缺口分析**（新建，核心算法要可答辩）：
  1. 目标岗位画像 = SQL 聚合明确的关键词、城市和可选过滤条件下的岗位数据；技能出现率使用“包含该技能的 distinct JD 数 / 过滤后有效 JD 数”，而不是原始词频。
  2. 技能需要经过别名归一化，并区分 `must_have`、`preferred`、技能类别和样本量；样本量过小时显示“仅供参考”。
  3. 简历技能 × 岗位画像 → 逐项输出：**已覆盖（附 JD 证据 + 简历证据）/ 部分覆盖 / 缺口（附建议学习或简历补写方向）**。
  4. 只输出透明的覆盖率及其分母、明细和证据，不输出单一“匹配度 87%”式黑盒分数；覆盖率不能替代录用概率判断。
- **报告拼装**：事实层由模板确定性拼装（SQL 统计表格、图表配置、证据引用）；LLM 只负责建议层的语言组织。STAR 改写不得虚构项目、技术、指标或结果；如果简历中没有量化证据，只能输出“建议补充”的占位提示。

### 评测体系（新建，与功能开发同级优先）

| 项 | 设计 |
|---|---|
| 数据划分 | `seed` 仅用于演示，`eval_dev` 用于 prompt/规则调整，`eval_test` 只在最终阶段运行；不得用同一批岗位数据同时开发和报告最终指标 |
| 评测集 | `eval_test` 至少 50 题中文问题 → golden SQL / 期望结果，覆盖：薪资分布（P50/P75，`quantile_cont`）、技能文档频率 top-N、经验×学历交叉、地区分布、公司维度、多条件联合查询；提供脱敏/合成 fixture 以保证公开仓库可复现 |
| 指标 | 首轮执行准确率、纠错后执行准确率、Self-Correction 恢复率（首次执行失败且经重试成功的比例）、平均尝试次数、P50/P95 端到端延迟、模型 token/cost |
| SQL 判定 | 结果集按明确规则进行顺序不敏感比较，并定义 NULL、浮点误差、排序、并列和空结果语义；必要时补充 SQL 结构或语义断言，避免不同错误 SQL 因结果偶然相同而被判为正确 |
| 技能抽取 | 对人工标注的脱敏 JD 计算 regex 与 LLM 的 precision / recall / F1、evidence 有效率；regex-LLM agreement 仅作为辅助指标 |
| 消融 | 纠错开/关、few-shot 示例开/关；固定模型、数据集和随机参数，记录配置版本，避免把数据泄漏误认为收益 |
| 形态 | pytest 风格 runner + JSON/Markdown 结果表格；前端评测页为可选项，不能阻塞核心评测 |

### 数据模型（草案，阶段 0 用金色数据集定稿）

**`crawl_runs`**（一次采集/ETL 运行，一行一个批次）

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | TEXT PK | 一次采集任务的唯一标识 |
| `keyword` / `city` | TEXT | 本次采集的目标参数 |
| `requested_pages` / `completed_pages` | INT | 请求页数和实际完成页数 |
| `status` | TEXT | `queued` / `running` / `blocked` / `succeeded` / `failed` / `cancelled` |
| `job_count` / `detail_count` | INT | 列表岗位数和有效详情数 |
| `started_at` / `finished_at` | TIMESTAMP | 采集任务时间 |
| `failure_code` | TEXT | 稳定的失败枚举，不直接依赖异常文本 |
| `scraper_version` / `etl_version` | TEXT | 可复现和审计所需的版本 |
| `dataset_version` | TEXT | 只有采集与 ETL 成功后才发布 |

`Coordinator` 的数据新鲜度检查以 `crawl_runs` 和 `dataset_version` 为准，而不是单独依赖岗位行上的最大时间戳。

**`jobs`**（规范化后的岗位快照，一行一个当前岗位）

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_id` | TEXT PK | md5(job_link)[:16]，继承 scraper；保留原始链接用于去重，不作为用户投递动作 |
| `title` / `company` | TEXT | company 来自详情页回填 |
| `salary_raw` | TEXT | 原始字符串（如 `40-70K·13薪`），审计用 |
| `salary_min_k` / `salary_max_k` | REAL | 统一折算为 K/月；面议为 NULL |
| `salary_months` | INT | 薪月数，默认 12 |
| `salary_annual_min_k` / `salary_annual_max_k` | REAL | min/max × months |
| `is_negotiable` | BOOLEAN | 薪资面议 |
| `salary_unit` / `salary_period` | TEXT | `K` / `万` / `元` 与 `month` / `day` / `year` 等原始口径 |
| `salary_parse_status` | TEXT | `parsed` / `negotiable` / `unsupported` / `invalid` |
| `salary_conversion_rule` | TEXT | `monthly_native` / `daily_x_22` / `annual_div_12` 等实际换算规则 |
| `city` / `district` | TEXT | 由 `location`（`上海·徐汇区·漕河泾`）拆解 |
| `experience` / `degree` | TEXT | 枚举（经验不限/应届/1-3年/…；学历不限/大专/本科/硕士/博士），由 `tags` 解析 |
| `company_scale` / `company_stage` / `company_industry` | TEXT | 原样 |
| `boss_active_status` / `welfare` / `job_link` / `keyword` / `salary_source` | TEXT | 原样 |
| `first_seen_at` / `last_seen_at` | TIMESTAMP | 岗位首次和最近一次在数据集中出现的时间 |
| `latest_scraped_at` | TIMESTAMP | 最近一次成功采集该岗位的时间；批次状态以 `crawl_runs` 为准 |
| `dataset_version` | TEXT | 当前规范化记录所属数据集版本 |

**`job_skills`**：保存岗位技能及抽取证据，不使用简单的 `job_id + skill + source` 三元组作为唯一事实。至少包含：

```text
job_id
skill_canonical
skill_display
category
source                 # list_tags / detail_tags / jd_regex / jd_llm
evidence_text
confidence
extractor_version
extracted_at
run_id
```

同一岗位可以在不同采集批次、抽取器版本和来源下保留多条记录，以支持 regex/LLM 对比和回溯。

**`job_details`**：`job_id` + `captured_at` + `jd_text`（完整 JD）+ `jd_hash` + `detail_status` + `company` + `skill_tags`。源 scraper 的 `jd` 字段在 ETL 中规范化为 `jd_text`；如果只保留最新 JD，仍需保留 `captured_at` 和 `jd_hash`。

**原始数据与运行时数据**：原始 scraper JSON 只作为本地可重放输入，不直接作为 NL2SQL 数据源；规范化 CSV 才进入 DuckDB。原始 JSON、简历文件和任务事件日志不得提交到公开仓库。

**薪资解析规则**（阶段 1 用金色数据集全量枚举补全）：

| salary_raw 形态 | 示例 | 规则 |
|---|---|---|
| K 区间 · N 薪 | `40-70K·13薪` | min=40, max=70, months=13 |
| K 区间 | `25-35K` | months=12 |
| 万/月 | `1-2万/月` | 转换为 10-20 K/月，记录原始单位 |
| 元/天 | `300-500元/天` | 折算 K/月 = 元/天 × 22 ÷ 1000，标记折算来源 |
| 年薪 | `年薪 30-50 万` | 转换为 250-416.67 K/月，记录 `annual_div_12`；若口径不明确则不强行转换 |
| 薪资面议 | `薪资面议` | `is_negotiable=TRUE`，min/max 为 NULL |
| 其余长尾 | `底薪 + 提成`、小时薪等 | 阶段 0 枚举；解析失败置 NULL 保留 raw，计数监控，不静默丢弃；不同计薪周期不直接混合计算月薪分位 |

## 详细开发计划（3–4 周，约 16–19 个工作日 + 2–3 天缓冲）

开发顺序按“先验证高风险基础设施，再扩展业务 Agent”调整。现有 45 条左右的脱敏/本地岗位数据可以立即作为开发 fixture；更大的真实数据集在后台采集，不得成为后续开发的硬阻塞。

### 阶段 0 · 工程契约与流式 Spike（1–2 天）——最高优先级

- [ ] 初始化根目录 monorepo：`backend / frontend / eval / docs / data`，统一 Python 3.11，建立统一启动和测试入口
- [ ] 定义 `UserRequest`、`TargetJobRequest`、`TaskSnapshot`、`SseEvent`、`CrawlRunResult`、`SqlAttempt`、`CareerReport` 等 Pydantic 契约
- [ ] 定义 `created / running / waiting_clarification / completed / failed / cancelled` 任务状态及错误枚举
- [ ] 明确 FastAPI 的异步边界：SSE endpoint 不直接调用阻塞型 `invoke()`、`urllib` 或 scraper；模型调用使用异步客户端，或由受控 worker 执行并通过事件日志回传
- [ ] 实现假的任务执行器：`POST /api/tasks` + `GET /api/tasks/{id}/events` + `GET /api/tasks/{id}`，验证事件顺序、心跳、断线恢复和重复事件去重
- [ ] 明确 Runtime Store：任务快照使用本地 JSON，SSE 事件使用 append-only JSONL；运行时文件不提交仓库
- [ ] 真实数据采集并行进行，不阻塞上述契约和 Spike

**阶段验收**：一个不依赖真实 LLM 和 scraper 的假任务可以从创建到完成；浏览器可看到结构化执行轨迹，刷新页面后仍能查询任务结果。

### 阶段 1 · ETL 与数据层（3–4 天）

- [ ] 定义源字段 → 规范字段映射：`salary → salary_raw`、`jd → jd_text`、顶层 `scraped_at → crawl_runs` 等
- [ ] 设计并实现 `crawl_runs`、`dataset_version` 和数据质量报告
- [ ] 薪资解析器 + 全格式边界 case 单测（区间、薪月、万/月、年薪、元/天、面议、未知长尾）
- [ ] tags / skills / welfare 管道符拆分，location 拆解，经验/学历枚举化，技能名归一化
- [ ] `etl.py`：JSON → 保留 raw → 按 `job_id` 去重/合并 → 产出 `data/databases/jobs_market/*.csv`
- [ ] 改造 Schema 生成器和通用 loader，生成岗位库 `_schema.json`（中文字段标注 + 数据画像 + 实际类型）
- [ ] 接入 `database.py` loader 与 `DATABASE_LABELS`；分析数据权限收敛为单用户本地应用
- [ ] ETL 单测（去重 / 增量 / 脏数据容错 / 数据类型 / 解析失败计数）

**阶段验收**：Schema 与实际 CSV 列完全一致，已支持的薪资格式全部通过测试，解析失败记录保留 raw 且有统计。

### 阶段 2 · QueryAgent 与 SQL Self-Correction（4–5 天）

- [ ] 移除 Schema 检索节点、`schema_store.json`、embedding/rerank 依赖与索引构建；以 `SchemaCatalog` 全量注入 prompt，并记录 token 规模
- [ ] 保留 sqlglot 只读安全校验和表白名单，增加结构化 `error_kind / retryable / attempt`
- [ ] 修改 fail-fast 守卫测试，实现 QueryAgent 的 SQL 生成 → 校验 → 执行 → 可修复错误回喂链路
- [ ] 明确最多 3 次 SQL 执行（首次 + 2 次修复），辅助工具调用不占用 SQL 修复预算
- [ ] Coordinator：新增 `career_advice` 路由、目标关键词/城市解析、任务状态和数据新鲜度检查
- [ ] 增加 QueryAgent SSE 事件：Schema 加载、SQL 生成、校验、执行、修复、完成/失败
- [ ] 图表工具扩展（line / 直方图）+ `ChartSpec` → ECharts 配置映射

**阶段验收**：查询成功、可修复错误可恢复、权限和安全错误不被模型绕过、所有 SQL 尝试可回溯。

### 阶段 3 · CareerAgent 最小闭环（4–5 天）

- [ ] 简历上传解析（文本 / 文本型 PDF）→ PII 脱敏 → 技能和项目证据结构化抽取
- [ ] JD 技能抽取：先完成可复现 regex 基线，再接入严格 Pydantic LLM structured output
- [ ] 设计技能 alias/canonical 归一化，记录类别、证据、置信度和 extractor version
- [ ] 构建岗位画像：岗位级技能出现率、薪资分位、经验/学历分布和样本量提示
- [ ] 覆盖 / 部分覆盖 / 缺口分析，输出 JD 证据 + 简历证据
- [ ] 报告拼装：事实层模板化，LLM 只负责建议层语言组织；STAR 改写禁止虚构经历和指标
- [ ] 暂不把 OCR、多格式文档、自动投递和复杂推荐加入 MVP

**阶段验收**：缺少简历或目标岗位参数时进入澄清；每一项覆盖结论都有证据；文本提取失败时明确失败，不生成伪分析。

### 阶段 4 · 评测、前端与演示（3–4 天 + 缓冲）

- [ ] `seed / eval_dev / eval_test` 三路数据分离；准备至少 50 个 NL2SQL case 和脱敏/合成 fixture
- [ ] pytest 风格 runner：首轮准确率、纠错后准确率、恢复率、尝试次数、P50/P95 延迟、token/cost
- [ ] 消融实验：纠错开/关、few-shot 开/关；记录模型、数据集和配置版本
- [ ] 拆分 782 行的 `App.vue`，接入 SSE 执行轨迹、Clarification、ECharts 与 Markdown 渲染
- [ ] 采集 CLI/任务 API 的状态展示；管理页作为可选项
- [ ] 种子数据一键启动（`make setup`）+ 演示录屏，**面试演示不依赖 live 爬取与登录**
- [ ] GitHub README（含免责声明、数据来源、隐私说明和量化指标）+ 简历 Highlight Bullets

> 缓冲使用原则：如果时间不足，优先保留离线 seed、QueryAgent Self-Correction、CareerAgent 证据报告和评测 runner；可以延期管理页、前端评测页、ECharts 高级图表以及 LLM/regex 的完整对比实验。若 SSE 集成确实延期，则保留同一任务/事件模型，切换为 REST + 轮询，并在简历中如实描述。

### MVP 与 Stretch 边界

**MVP 必须完成**：

- 离线 seed 数据和可重放的 ETL；
- `jobs`、`job_skills`、`job_details` 查询，以及全量 Schema 注入；
- SQL 只读安全校验和最多 2 次 Self-Correction；
- 至少一个完整 `career_advice` 流程：简历证据 → 岗位画像 → 技能覆盖/缺口 → 报告；
- SSE 或同一任务模型上的 REST 轮询兜底；
- 至少 50 题评测 runner 和可复现结果；
- README、隐私说明、合规边界和离线演示脚本。

**Stretch 可延期**：

- 采集管理页，先保留 CLI + 任务 API；
- 前端评测页和 ECharts 高级配置；
- LLM/regex 技能抽取的完整对比实验；
- OCR、多格式文档、多用户权限、多库查询和自动投递能力。

## 风险与兜底

| 风险 | 等级 | 缓解措施 |
|---|---|---|
| SSE + LangGraph 流式集成（两项目均无完整先例） | 高 | 阶段 0 先完成假的任务流和事件协议；使用同一任务模型支持 SSE 与 REST 轮询；不把“已安装 sse-starlette”当作完成依据 |
| 同步 scraper/LLM 阻塞 FastAPI | 高 | scraper 优先独立子进程；模型调用使用异步客户端或 worker；对同一 Chrome profile 加锁并支持取消/超时 |
| BOSS 风控或登录态失效 | 高 | 阶段 0 使用现有本地 fixture 立即开发；较大数据集后台采集；开发 / 评测 / 演示均不依赖 live 爬取 |
| AskData fail-fast 设计与守卫测试冲突 | 中 | 先定义可修复/不可修复错误；保留 fail-fast 作为纠错耗尽后的最终行为，并记录全部 SQL 尝试 |
| Schema RAG 移除不完整 | 中 | 同步删除 SchemaIndex、检索状态、embedding/rerank 配置、管理接口、前端类型和相关测试；以 SchemaCatalog 与 token 统计验收 |
| 薪资长尾格式解析遗漏 | 中 | 阶段 1 用真实数据枚举；失败置 NULL 保留 raw，记录 parse_status 和计数，不静默丢弃或混入分位计算 |
| JD/简历技能抽取事实错误 | 中 | LLM 输出严格 Pydantic 校验；技能保留原文证据；以人工标注集计算 precision/recall/F1；报告层分离事实和建议 |
| 简历 PII 泄漏 | 高 | 外部 LLM 调用前脱敏；日志/SSE 不输出完整简历；设置文件大小/页数限制；提供 resume 删除接口和 README 隐私说明 |
| 两项目代码风格差异（单文件 vs 分层） | 低 | 采集侧保持独立 worker/子进程调用，不强行重构进 AskData 结构；通过 Pydantic 契约隔离边界 |

## 明确不做（Scope Cuts）

- Qdrant / FAISS 等向量库与 embedding / rerank API（schema 全量注入后无场景；JD 匹配用可解释覆盖分析）
- PostgreSQL / SQLite 业务数据持久层（DuckDB 已锁定；运行时任务 JSON 和事件 JSONL 不属于分析业务数据库）
- 对话内同步触发爬取（阻塞 graph 数分钟，改为独立采集任务 + Coordinator 引导）
- 黑盒"人岗匹配度"分数
- AskData 多库 / 多角色权限体系（单用户本地应用）
- NER 模型训练（用 LLM structured output 替代）
- 采集端指纹伪造 / 验证码绕过 / 代理池（合规红线，永久不做）

## 合规红线

- 只做被动捕获（CDP Network 域旁听页面自身请求），零注入；`_security_check` 验证页交人工处理，不自动绕过（继承 scraper 红线）
- 继承请求预算（500 次/会话）与随机间隔、页数上限（10 页）
- 公开仓库不提交大规模爬取数据、登录态、Cookie、Chrome profile 或完整 JD；仅放脱敏/合成小样本；README 保留免责声明（仅供学习研究，不得商用或对目标站点造成负担）
- 不自动发送消息、自动投递或执行任何需要用户确认的求职动作；采集任务只能处理用户授权的本地登录环境
- 简历属于高敏感个人数据：默认本地短期保存，外部 LLM 调用前进行 PII 脱敏，日志和 SSE 不保存完整简历内容
- 开发与演示基于冻结数据集，避免高频访问目标站点

## 简历呈现建议（数字以评测结果回填）

- 基于 LangGraph 设计多 Agent 求职决策系统：Coordinator、MarketDataAgent、QueryAgent、CareerAgent 通过 Pydantic 契约协作，支持意图路由、数据新鲜度检查和 interrupt 人机澄清，SSE 输出结构化执行轨迹与报告
- 实现 SQL Self-Correction 纠错环：50+ 题中文 NL2SQL 基准执行准确率 X%，纠错使失败查询恢复率提升 Y 个百分点（消融验证）
- CDP 被动捕获应对前端字体反爬：明文薪资采集，风控码分类（31/37）与失败状态上报
- 设计薪资数值化 ETL（区间/薪月/日薪折算）入 DuckDB，支持 P50/P75 分位等聚合问数
- 可解释技能覆盖分析：LLM 结构化抽取 JD 技能（与词频基线一致率 Z%），输出逐项证据的技能缺口报告

## 参考项目

- [boss-zhipin-scraper README](boss-zhipin-scraper/README.md) — CDP 被动捕获爬虫与数据格式
- [AskData README](AskData/README.md) — NL2SQL 引擎、LangGraph 工作流与安全校验层
