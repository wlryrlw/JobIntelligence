# JobIntel 工程规范

本文件适用于 JobIntel 仓库根目录及其子目录。更深层目录中的 `AGENTS.md` 可以为某个集成提供更具体的局部规则；局部规则只在其目录作用域内生效，不应覆盖 JobIntel 的整体架构边界。

## 项目边界

JobIntel 是一个基于 Multi-Agent、NL2SQL 和岗位数据 ETL 的本地求职决策系统。主要目录职责如下：

- `backend/`：FastAPI、任务工作流、Agent、ETL、查询引擎、运行时存储和 API。
- `frontend/`：Vue 前端和任务执行轨迹展示。
- `integrations/`：外部项目或工具集成；每个集成可以有自己的局部规范。
- `data/`：可复现的 fixture、种子数据和公开数据说明。
- `eval/`：评测数据、runner 和结果说明。
- `docs/`：设计文档、历史文档和学习笔记，不是临时构建产物。

`integrations/boss_zhipin/` 是独立的 BOSS 直聘采集集成。采集侧的 CDP、Chrome、登录态和风控规则以该目录下的局部 `AGENTS.md` 为准；其他模块不得绕过受控边界直接驱动 scraper。

## 开发环境与命令

- 使用 Python 3.11；依赖和锁文件统一由根目录 `pyproject.toml`、`uv.lock` 管理。
- 从仓库根目录执行 `uv sync` 和 `uv run ...`，不要为了某个子模块创建另一套项目虚拟环境。
- 完整后端和集成测试使用 `uv run pytest`。修改单个模块时可以先运行其对应测试，再运行完整测试。
- 默认使用本地 fixture 和 mock 验证逻辑。只有修改真实外部集成链路时，才执行该集成要求的 live smoke test。

## 架构与数据契约

- Agent 之间通过经过 Pydantic 校验的输入、输出和状态对象通信；核心链路不能退化为无约束的任意 `dict`。
- 外部 API、文件、数据库、用户输入和 LLM 输出都必须在进入核心逻辑前做运行时校验。
- 优先使用纯函数和不可变状态转换；不要通过隐式全局状态修改其他 Agent 的内部状态。
- Coordinator 负责路由、资源检查、任务状态和下游任务协调；不直接执行 scraper 或 SQL。
- QueryAgent 只能通过只读安全层访问允许的数据集；模型生成的 SQL 不是安全证明。
- CareerAgent 只能使用可追溯的岗位证据和简历证据，不得创造简历事实、指标或经历。
- FastAPI 事件循环不能被同步 scraper、LLM、文件或数据库操作阻塞。阻塞型工作必须使用异步客户端、受控 worker 或明确的线程边界，并通过任务事件回传状态。

## 错误处理、隐私与合规

- 禁止使用 bare `except:`，也不能静默吞掉异常。捕获具体异常，补充请求上下文、状态码或受限响应片段，并在无法恢复时显式失败。
- 外部 LLM/API 调用应使用有界重试和退避；不可修复的错误不能通过无限重试或改变安全边界来掩盖。
- 任务快照、SSE 和日志只记录完成任务所需的结构化信息，不暴露模型隐藏思维链、完整简历文本、访问凭证或不必要的 PII。
- 简历默认本地短期保存；发送给外部模型前必须脱敏，并提供按 `resume_id` 删除原始文件和抽取结果的能力。
- 岗位采集必须遵守 `integrations/boss_zhipin/` 的合规边界：不绕过验证码、风控、登录限制或平台安全措施。

## 测试与验证

- 修改功能时补充最小但高价值的测试，优先覆盖状态边界、错误路径、权限边界、数据质量和外部输入异常。
- 阶段 0 重点验证任务状态、Runtime Store、SSE 事件顺序、重连和去重；不依赖真实 LLM 或 scraper。
- 阶段 1 重点验证 ETL、薪资解析、去重合并、数据质量报告、CSV/Schema 一致性和数据版本发布。
- 阶段 2 重点验证 SchemaCatalog、SQL 只读校验、表白名单、有限 Self-Correction、Coordinator 资源门控和结构化 Query 事件。
- 真实 Chrome 验证只对实际修改 `integrations/boss_zhipin/` 抓取、登录、详情或 CDP 链路的改动适用；普通后端、前端和离线 ETL 改动不应被 live 爬取阻塞。

## 文件与协作纪律

- 保留用户已有的未提交修改；编辑前先检查工作树，避免覆盖无关改动。
- 不使用破坏性 Git 操作，不主动执行 `git reset --hard`、`git checkout --` 或大范围删除。
- 不主动创建 commit、分支、PR 或执行远程发布，除非用户明确要求。
- `docs/Design document.md` 是项目正式设计资料；`docs/notes/` 是学习笔记。除非用户明确要求，不修改或填写 `docs/notes/`。
- 修改 API、行为或公开配置时，更新相应的项目文档和测试；不要把 scraper 的 README 双语同步或集成版本号规则扩展到整个 JobIntel。
