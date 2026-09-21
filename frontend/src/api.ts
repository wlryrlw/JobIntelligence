import type { AuthUser, LoginResponse, QueryResult, SavedMemory, SchemaField, SchemaTable, TaskEvent, TaskSnapshot, WorkspaceConfig } from "./types"

const API_BASE = import.meta.env.VITE_API_BASE ?? ""
const TOKEN_KEY = "offeragent_access_token"

function token() {
  return sessionStorage.getItem(TOKEN_KEY)
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 90_000)
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
        ...options?.headers,
      },
    })
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
  schema: () => request<SchemaTable[]>("/api/schema"),
  query: (query: string, workspace: WorkspaceConfig, sessionId: string) =>
    request<QueryResult>("/api/query", {
      method: "POST",
      body: JSON.stringify({ query, session_id: sessionId, workspace }),
    }),
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
  saveField: (tableId: string, field: SchemaField) =>
    request<{ saved: boolean }>("/api/memories/fields", {
      method: "POST",
      body: JSON.stringify({
        table_id: tableId,
        name: field.name,
        label: field.label,
        field_type: field.type,
      }),
    }),
  createTask: (text: string, sessionId: string) =>
    request<TaskSnapshot>("/api/tasks", {
      method: "POST",
      body: JSON.stringify({ text, session_id: sessionId }),
    }),
  streamTaskEvents: async (taskId: string, onEvent: (event: TaskEvent) => void, signal?: AbortSignal) => {
    const response = await fetch(`${API_BASE}/api/tasks/${encodeURIComponent(taskId)}/events`, {
      headers: { Accept: "text/event-stream" },
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
    request<{ deleted: boolean }>(`/api/memories/${encodeURIComponent(memoryId)}`, {
      method: "DELETE",
    }),
}
