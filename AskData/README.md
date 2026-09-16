# AskData Studio

AskData Studio 是一个本地运行的自然语言问数项目。前端使用 Vue 3，后端使用 FastAPI 和 LangGraph；系统能够检索字段级 Schema、生成并执行 SQL，并以表格和文字形式返回结果。

## 环境要求

- Python 3.11
- Node.js 20.19 或更高版本
- 可用的大模型、Embedding 和 Rerank API Key

以下命令均从项目根目录开始执行，不依赖固定的本机路径。

## 安装环境

### 后端

Windows PowerShell：

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS 或 Linux：

```bash
cd backend
python3.11 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

编辑 `backend/.env`，至少填写 API Key。默认配置使用 `text-embedding-v4` 和 `qwen3-rerank`：

```env
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.7-plus
EMBEDDING_MODEL=text-embedding-v4
RERANK_MODEL=qwen3-rerank
```

### 前端

```powershell
cd frontend
npm install
```

## 启动服务

分别打开两个终端。

后端（Windows）：

```powershell
cd backend
.\.venv\Scripts\python.exe run.py
```

后端（macOS 或 Linux）：

```bash
cd backend
./.venv/bin/python run.py
```

前端：

```powershell
cd frontend
npm run dev
```

启动后访问：

- 前端页面：`http://127.0.0.1:5173`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/api/health`

## 使用方法

使用内置账号登录：

- 管理员：`admin` / `admin123`
- 电商运营用户：`sales` / `sales123`（访问 `ecommerce_ops`）

登录后可直接输入问题，例如：

- 查询本月各地区销售额
- 按客户等级统计本月销售额
- 对比本月各地区销售额和销售目标

右侧的“查询字段”用于明确本次 SQL 必须使用的字段；保存的结果表用于后续问答和综合分析。查询完成后可以查看 SQL、翻页、导出 Excel，并按需保存字段或结果。
