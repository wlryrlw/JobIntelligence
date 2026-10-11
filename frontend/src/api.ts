import type {
  AuthUser,
  CrawlBrowserResult,
  CrawlEvent,
  CrawlOptions,
  CrawlPreflightResult,
  CrawlRequest,
  CrawlRunSnapshot,
  LoginResponse,
  MarketDataDeleteResult,
  MarketDataRun,
  MarketDataSummary,
  QueryResult,
  QueryProgressEvent,
  ResumeUploadResponse,
  SavedMemory,
  TaskEvent,
  TaskSnapshot,
  WorkspaceConfig,
  TargetJobRequest,
} from "./types"

const API_BASE = import.meta.env.VITE_API_BASE ?? ""
const TOKEN_KEY = "offeragent_access_token"

function token() {
  return sessionStorage.getItem(TOKEN_KEY)
}

async function request<T>(path: string, options?: RequestInit, timeoutMs = 90_000): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  const headers = new Headers(options?.headers)
  if (!(options?.body instanceof FormData)) headers.set("Content-Type", "application/json")
  if (token()) headers.set("Authorization", `Bearer ${token()}`)
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, signal: controller.signal, headers })
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("请求超时，请检查模型接口配置后重试")
    }
    throw new Error("无法连接后端服务，请先启动 FastAPI（127.0.0.1:8000）")
  } finally {
    window.clearTimeout(timeout)
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail ?? "服务暂时不可用")
  }
  return response.json() as Promise<T>
}

export const api = {
  hasSession: () => Boolean(token()),
  login: async (username: string, password: string) => {
    const result = await request<LoginResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    })
    sessionStorage.setItem(TOKEN_KEY, result.access_token)
    return result.user
  },
  me: () => request<AuthUser>("/api/auth/me"),
  logout: async () => {
    try {
      if (token()) await request<{ logged_out: boolean }>("/api/auth/logout", { method: "POST" })
    } finally {
      sessionStorage.removeItem(TOKEN_KEY)
    }
  },
  clearSession: () => sessionStorage.removeItem(TOKEN_KEY),
  query: (
    query: string,
    workspace: WorkspaceConfig,
    sessionId: string,
    careerContext?: { resume_id?: string; target?: TargetJobRequest },
    taskId?: string,
  ) => request<QueryResult>("/api/query", {
    method: "POST",
    body: JSON.stringify({
      query,
      session_id: sessionId,
      workspace,
      ...(taskId ? { task_id: taskId } : {}),
      ...(careerContext?.resume_id ? { resume_id: careerContext.resume_id } : {}),
      ...(careerContext?.target ? { target: careerContext.target } : {}),
    }),
  }, 75_000),
  uploadResume: (file: File) => {
    const form = new FormData()
    form.append("file", file)
    return request<ResumeUploadResponse>("/api/resumes", { method: "POST", body: form })
  },
  deleteResume: (resumeId: string) =>
    request<{ deleted: boolean; resume_id: string }>(`/api/resumes/${encodeURIComponent(resumeId)}`, { method: "DELETE" }),
  clarify: (taskId: string, optionId: string) =>
    request<QueryResult>(`/api/tasks/${taskId}/clarify`, {
      method: "POST",
      body: JSON.stringify({ option_id: optionId }),
    }),
  save: (taskId: string) =>
    request<{ saved: boolean }>("/api/memories", {
      method: "POST",
      body: JSON.stringify({ task_id: taskId }),
    }),
  memories: () => request<SavedMemory[]>("/api/memories"),
  crawlOptions: () => request<CrawlOptions>("/api/crawl/options"),
  marketDataSummary: () => request<MarketDataSummary>("/api/market-data/summary"),
  streamQueryEvents: async (
    taskId: string,
    onEvent: (event: QueryProgressEvent) => void,
    signal?: AbortSignal,
  ) => {
    const headers = new Headers({
      Accept: "text/event-stream",
      ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
    })
    let response: Response | null = null
    for (let attempt = 0; attempt < 20; attempt += 1) {
      response = await fetch(`${API_BASE}/api/query-runs/${encodeURIComponent(taskId)}/events`, { headers, signal })
      if (response.status !== 404) break
      await new Promise((resolve) => window.setTimeout(resolve, 100))
    }
    if (!response || !response.ok || !response.body) throw new Error("问答进度 SSE 连接失败")
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    let eventName = "message"
    let eventId = ""
    let dataLines: string[] = []
    let terminalEvent = false
    const flush = () => {
      if (!dataLines.length) return false
      const parsed = JSON.parse(dataLines.join("\n")) as QueryProgressEvent
      onEvent({ ...parsed, event_id: parsed.event_id || eventId, event_type: parsed.event_type || eventName })
      terminalEvent = ["completed", "failed", "waiting_clarification"].includes(parsed.payload?.status || "")
      eventName = "message"
      eventId = ""
      dataLines = []
      return terminalEvent
    }
    while (true) {
      const chunk = await reader.read()
      buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done })
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() || ""
      for (const frame of frames) {
        for (const line of frame.split(/\r?\n/)) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim()
          else if (line.startsWith("id:")) eventId = line.slice(3).trim()
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim())
        }
        if (flush()) {
          // 进度流是旁路信号；收到终态后主动释放 reader，不能等待服务端的连接收尾。
          void reader.cancel().catch(() => undefined)
          return
        }
      }
      if (chunk.done) {
        flush()
        break
      }
    }
  },
  marketDataRuns: () => request<MarketDataRun[]>("/api/market-data/runs"),
  deleteMarketDataRun: (runId: string) =>
    request<MarketDataDeleteResult>(`/api/market-data/runs/${encodeURIComponent(runId)}`, { method: "DELETE" }),
  preflightCrawl: (runSmokeTest: boolean) =>
    request<CrawlPreflightResult>("/api/crawl/preflight", {
      method: "POST",
      body: JSON.stringify({ run_smoke_test: runSmokeTest }),
    }),
  startCrawlBrowser: () =>
    request<CrawlBrowserResult>("/api/crawl/browser/start", {
      method: "POST",
      body: JSON.stringify({ confirmation: true }),
    }),
  stopCrawlBrowser: () => request<CrawlBrowserResult>("/api/crawl/browser/stop", { method: "POST" }),
  createCrawlRun: (payload: CrawlRequest) =>
    request<CrawlRunSnapshot>("/api/crawl-runs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getCrawlRun: (runId: string) => request<CrawlRunSnapshot>(`/api/crawl-runs/${encodeURIComponent(runId)}`),
  cancelCrawlRun: (runId: string) =>
    request<CrawlRunSnapshot>(`/api/crawl-runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }),
  resumeCrawlRun: (runId: string) =>
    request<CrawlRunSnapshot>(`/api/crawl-runs/${encodeURIComponent(runId)}/resume`, { method: "POST" }),
  streamCrawlEvents: async (
    runId: string,
    onEvent: (event: CrawlEvent) => void,
    signal?: AbortSignal,
    lastEventId?: string,
  ) => {
    const headers = new Headers({
      Accept: "text/event-stream",
      ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
    })
    if (lastEventId) headers.set("Last-Event-ID", lastEventId)
    const response = await fetch(`${API_BASE}/api/crawl-runs/${encodeURIComponent(runId)}/events`, { headers, signal })
    if (!response.ok || !response.body) throw new Error("采集进度 SSE 连接失败")
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    let eventName = "message"
    let eventId = ""
    let dataLines: string[] = []
    const flush = () => {
      if (!dataLines.length) return
      const raw = dataLines.join("\n")
      const parsed = JSON.parse(raw) as CrawlEvent
      onEvent({ ...parsed, event_id: parsed.event_id || eventId, event_type: parsed.event_type || eventName })
      eventName = "message"
      eventId = ""
      dataLines = []
    }
    while (true) {
      const chunk = await reader.read()
      buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done })
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() || ""
      for (const frame of frames) {
        for (const line of frame.split(/\r?\n/)) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim()
          else if (line.startsWith("id:")) eventId = line.slice(3).trim()
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim())
        }
        flush()
      }
      if (chunk.done) {
        flush()
        break
      }
    }
    return request<CrawlRunSnapshot>(`/api/crawl-runs/${encodeURIComponent(runId)}`)
  },
  createTask: (text: string, sessionId: string) =>
    request<TaskSnapshot>("/api/tasks", {
      method: "POST",
      body: JSON.stringify({ text, session_id: sessionId }),
    }),
  streamTaskEvents: async (taskId: string, onEvent: (event: TaskEvent) => void, signal?: AbortSignal) => {
    const response = await fetch(`${API_BASE}/api/tasks/${encodeURIComponent(taskId)}/events`, {
      headers: { Accept: "text/event-stream", ...(token() ? { Authorization: `Bearer ${token()}` } : {}) },
      signal,
    })
    if (!response.ok || !response.body) throw new Error("任务 SSE 连接失败")
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    let eventName = "message"
    let eventId = ""
    let dataLines: string[] = []
    const flush = () => {
      if (!dataLines.length) return
      const raw = dataLines.join("\n")
      try {
        const parsed = JSON.parse(raw) as TaskEvent
        onEvent({ ...parsed, event_id: parsed.event_id || eventId, event_type: parsed.event_type || eventName })
      } catch {
        throw new Error("任务 SSE 事件格式无效")
      }
      eventName = "message"
      eventId = ""
      dataLines = []
    }
    while (true) {
      const chunk = await reader.read()
      buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done })
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() || ""
      for (const frame of frames) {
        for (const line of frame.split(/\r?\n/)) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim()
          else if (line.startsWith("id:")) eventId = line.slice(3).trim()
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim())
        }
        flush()
      }
      if (chunk.done) {
        flush()
        break
      }
    }
    return request<TaskSnapshot>(`/api/tasks/${encodeURIComponent(taskId)}`)
  },
  deleteMemory: (memoryId: string) =>
    request<{ deleted: boolean }>(`/api/memories/${encodeURIComponent(memoryId)}`, { method: "DELETE" }),
}
