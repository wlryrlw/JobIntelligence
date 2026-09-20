# AGENTS.md

本文件只适用于 `integrations/boss_zhipin/` 及其子目录。它描述 BOSS 直聘 CDP 采集集成的局部规则；OfferAgent 根目录、`backend/`、`frontend/`、`eval/`、`data/` 和 `docs/` 以仓库根目录的 `AGENTS.md` 为准。先读适用范围内的规则，再动代码。

## 这是什么

`boss-zhipin-scraper` —— 通过 Chrome CDP（远程调试端口）连接**用户本人已登录的 Chrome**，抓取 BOSS直聘的公开职位数据（列表 + 详情），并可生成求职分析摘要。仅用于个人求职分析，非大规模爬虫（见 `CONTRIBUTING.md` 的合规一节）。

## 目录结构

```
scripts/boss_cdp_raw.py   # 核心：抓取 + CLI 主入口（长单文件，行数以实际为准）
scripts/job_summary.py    # 抓取结果 → Markdown 求职分析摘要
data/city_codes.json      # 全量城市码表（300+ 城市，外置；见下）
tests/test_chrome_setup.py    # unittest，全 mock，不依赖真实 Chrome/网络
tests/test_job_summary.py     # 摘要测试
../../pyproject.toml      # 仓库唯一 Python 依赖真源
../../uv.lock             # 仓库唯一 Python 锁文件
SKILL.md / README(.en).md / CHANGELOG.md / CONTRIBUTING.md
```

**重要边界：本集成的核心抓取逻辑都放 `scripts/boss_cdp_raw.py`，不要随手在本集成内拆出新的抓取模块**（见 `CONTRIBUTING.md`「单文件原则」）。这条单文件规则不适用于 OfferAgent 的 `backend/`、`frontend/` 或其他模块；OfferAgent 根目录的 `docs/` 是正式项目资料，不适用原始 scraper 项目的“本地产物、不提交”约定。**例外**：`data/city_codes.json` 是城市码表数据（非逻辑代码），外置便于用户查看支持哪些城市；改它要同步跑 `tests.test_chrome_setup` 的城市码表防回归测试。

## 环境与命令

- Python 统一使用仓库根目录 `.python-version` 指定的 **3.11**；依赖由根目录 `pyproject.toml` 和 `uv.lock` 管理。请从仓库根目录执行 `uv sync`，不要在集成目录创建独立环境。
- 跑测试：`uv run pytest integrations/boss_zhipin/tests`（无需 Chrome / 联网，全 mock）。改了 `job_summary` 再加跑 `uv run pytest integrations/boss_zhipin/tests/test_job_summary.py`。
- 语法自检：`uv run python -m py_compile integrations/boss_zhipin/scripts/boss_cdp_raw.py`。
- 实跑抓取需要先启动带调试端口的 Chrome：`uv run python integrations/boss_zhipin/scripts/boss_cdp_raw.py --setup-chrome`（开 `127.0.0.1:9222`，默认端口见 `DEFAULT_CDP_PORT`），登录后在**另一个终端**跑抓取命令。Chrome 关了端口就没了。

## 改代码时的硬规则

1. **BOSS 集成版本号四处一致**：`scripts/boss_cdp_raw.py` 的 `__version__`（第 22 行附近）、仓库根目录 `pyproject.toml` 的 `[tool.offeragent.integrations.boss_zhipin]`、本目录 `SKILL.md`、本目录 `README.md` 必须同步，否则 `VersionConsistencyTests` 会挂。该版本号只表示 BOSS 集成本身，不表示 OfferAgent 整体版本。改版本号时四处一起改。
2. **异常处理**：禁止 bare `except:`，必须捕获具体类型（`requests.ConnectionError`、`json.JSONDecodeError` 等），和现有代码保持一致。
3. **改了用户可见行为 → 更新 `README.md`；有意义变更 → `CHANGELOG.md` 顶部加一条。**
4. **README 双语同步**：`README.md`（中文）和 `README.en.md`（英文）必须保持一致，改了其中一个就要同步另一个。
5. **commit message 用 Conventional Commits**（`feat:` / `fix:` / `docs:` / `optimize:` / `refactor:` 等，见 `CONTRIBUTING.md`）。
6. **动抓取链路必须真机验证**：凡改本集成的列表抓取、登录探测、详情链路的改动，合并前要在真机过一遍 `--check` → `--smoke` → `--pages 2 --no-detail`（Chrome 需已启动且已登录）。全 mock 单测覆盖不到浏览器的焦点、定时器节流、SPA 行为和风控——焦点仿真 bug 曾 96 个单测全绿而真机翻页静默失败。一次性验证脚本（spike）放 `/tmp`，不进仓库；普通 OfferAgent 后端、前端和离线 ETL 改动不以 live 爬取作为验收前置条件。
7. **合规红线**：只做被动捕获（Network 域旁听页面自身请求）和请求节奏控制。**禁止**主动伪装类手段：指纹伪造、验证码绕过、代理池轮换。遇到 `_security_check` 验证页时提示用户人工处理，不自动绕过。

## 架构关键点（容易踩坑）

- `scripts/boss_cdp_raw.py` 是一个**长单文件**，包含：`CDPSession` 类（WebSocket 连 CDP，含事件缓冲 `events` / `drain_events`）、`NetworkJoblistCapture`（列表被动捕获）、`scrape_list`（列表抓取）、`scrape_details`（详情抓取）、`main`（argparse）。城市码表外置到 `data/city_codes.json`，`resolve_city` 查询链为「本地静态码表 → 运行时拉 BOSS 接口 → 原样兜底」。
- **列表页 vs 详情页路径完全不同**：列表页导航真实搜索页，通过 CDP `Network` 域**被动捕获页面自身发出的 joblist 响应**（不注入任何请求，背景见 `NetworkJoblistCapture` 头部注释）；翻页靠滚动触发页面自身的无限滚动加载。详情页通过新开 tab → `Page.navigate` → 注入 JS 提取。改其中一条路径时，另一条不受影响。
- **BOSS 页面行为事实**（2026-08 实测，写代码前先知道）：搜索页无翻页控件、纯无限滚动，URL 带 `&page=N` 直跳无效（SPA 始终请求第 1 页）；页面自身请求是 POST + form body（URL 只有时间戳）；每页 15 条，`zpData.hasMore` 判断是否最后一页；反复自动化导航可能触发 `_security_check` 验证页。
- **CDP target 焦点/可见性不变量**：统一通过 `create_page_session` 创建页面；自动化 target 默认后台打开并注册 visibility override + **焦点仿真**（`Emulation.setFocusEmulationEnabled`），两者都不可省——后台页真实的 `hidden/hasFocus=false` 状态会让无限滚动加载静默失效（实测 JS override 单独不生效，焦点仿真才解决），同时避免抢用户前台焦点（issue #18/#29）。只有需要用户操作的 `wait_for_login` 显式传 `background=False`。不要绕过 helper 直接新增 `Target.createTarget`。
- 同一个 Chrome 实例的默认 browser context 下，新开 target **本就共享 cookies**，不要被「新 tab 丢 cookie」的直觉误导。
- `require_runtime_dependencies("requests", "websocket")` 在多个入口前置检查依赖，缺了会提示安装。

## 独立维护本集成时的提交流程

以下流程适用于把 `integrations/boss_zhipin/` 作为独立上游项目维护或向其上游仓库提交贡献。在 OfferAgent 主仓库内开发时，分支、commit、PR 和发布操作以根目录 `AGENTS.md`、当前仓库实际配置和用户明确要求为准。

独立上游项目默认分支为 `master`，fork/分支工作流：从 `master` 拉新分支（`fix/...`、`feat/...`）→ 改代码补测试 → push → PR。一个 PR 只做一件事。**审 PR / 拉分支前先 `git fetch origin`**——本地 master 落后时 diff 范围会失真（曾把已合并的 #43 误判为 PR 夹带改动）。

**合并惯例**：squash merge，标题带 `(#PR号)` 后缀（如 `fix: ...(#55)`）；PR 描述含 `Fixes #N` / `Closes #N` 时合并会自动关闭对应 issue，只提 `#N`（普通引用）则不会。

**先开 issue 再动手**：非平凡的改动（bug 修复、新功能、文档补充）按仓库 `CONTRIBUTING.md` 的规范，先在 Issues 开一条说明「改什么 / 为什么 / 怎么改」，讨论清楚后再起新分支提交。issue 正文要结构化（问题 / 现状 / 根因 / 建议 / 影响），并标注改动范围（哪些逻辑受影响、哪些不动）。

**issue 分诊打标**：处理/关闭 issue 时按 `CONTRIBUTING.md` 的「Issue 标签约定」打标签；`wontfix` / `invalid` 关闭前必须留说明理由的回复。
