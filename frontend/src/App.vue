<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue"
import { api } from "./api"
import ReportCard from "./components/ReportCard.vue"
import ResultTableCard from "./components/ResultTableCard.vue"
import ClarificationPanel from "./components/ClarificationPanel.vue"
import DemoTaskPanel from "./components/DemoTaskPanel.vue"
import ExecutionTimeline from "./components/ExecutionTimeline.vue"
import type { AuthUser, QueryResult, ReportDataSource, SavedMemory, SchemaField, SchemaTable, WorkspaceConfig } from "./types"

interface ConversationTurn {
  query: string
  result: QueryResult
}

interface ConversationRecord {
  id: string
  title: string
  updatedAt: number
  turns: ConversationTurn[]
  workspace: WorkspaceConfig
}

interface HistoricalResultTable {
  taskId: string
  title: string
  query: string
  rowCount: number
  columns: string[]
  rows: Record<string, string | number | null>[]
}

const SAVED_TABLE_LIMIT = 8
const FIELD_LIBRARY_LIMIT = 12
const prompts = ["上海 AI Agent 岗位最常见的技能有哪些？", "统计杭州 Python 后端岗位的薪资分布", "对比北京和上海的 AI 岗位要求"]
type SubmitShortcut = "enter" | "metaEnter"
const input = ref("")
const loading = ref(false)
const pendingQuery = ref("")
const error = ref("")
const schema = ref<SchemaTable[]>([])
const savedMemories = ref<SavedMemory[]>([])
const workspace = ref<WorkspaceConfig>({})
const conversations = ref<ConversationRecord[]>([])
const activeConversationId = ref("")
const expandedSql = ref<string | null>(null)
const leftOpen = ref(false)
const rightOpen = ref(false)
const conversationScroll = ref<HTMLElement | null>(null)
const authUser = ref<AuthUser | null>(null)
const authReady = ref(false)
const loginLoading = ref(false)
const loginUsername = ref("admin")
const loginPassword = ref("admin123")
const submitShortcut = ref<SubmitShortcut>("enter")
type ContextDropTarget = "field" | "result" | null
const librarySection = ref<HTMLElement | null>(null)
const dragTarget = ref<ContextDropTarget>(null)
const composing = ref(false)

const activeConversation = computed(() =>
  conversations.value.find((item) => item.id === activeConversationId.value),
)
const confirmedFields = computed(() => workspace.value.schema_fields ?? [])
const analysisTableIds = computed(() => workspace.value.analysis_table_ids ?? [])
const historicalResultTables = computed<HistoricalResultTable[]>(() => {
  return savedMemories.value
    .filter((item) => item.kind === "result_table" && item.task_id && item.rows?.length)
    .map((item) => ({
      taskId: item.task_id!,
      title: item.title || "查询结果",
      query: item.query || "",
      rowCount: item.rows?.length || 0,
      columns: item.columns || [],
      rows: item.rows || [],
    }))
    .slice(0, SAVED_TABLE_LIMIT)
})
const reportDataSources = computed<ReportDataSource[]>(() => {
  const sources = new Map<string, ReportDataSource>()
  for (const conversation of conversations.value) {
    for (const turn of conversation.turns) {
      if (!turn.result.rows.length) continue
      sources.set(turn.result.task_id, {
        taskId: turn.result.task_id,
        title: turn.result.result_title || "查询结果",
        columns: turn.result.columns,
        rows: turn.result.rows,
      })
    }
  }
  for (const table of historicalResultTables.value) {
    sources.set(table.taskId, {
      taskId: table.taskId,
      title: table.title,
      columns: table.columns,
      rows: table.rows,
    })
  }
  return [...sources.values()]
})
const selectedAnalysisTables = computed(() =>
  analysisTableIds.value
    .map((id) => historicalResultTables.value.find((table) => table.taskId === id))
    .filter((table): table is HistoricalResultTable => Boolean(table)),
)
const recentFields = computed(() => {
  const items: { field: SchemaField; tableId: string }[] = []
  const keys = new Set<string>()
  const add = (fieldName: string, tableId?: string) => {
    const table = (tableId ? schema.value.find((item) => item.id === tableId) : undefined)
      ?? schema.value.find((item) => item.fields.some((field) => field.name === fieldName))
    const field = table?.fields.find((item) => item.name === fieldName)
    const resolvedTableId = table?.id
    const key = `${resolvedTableId}:${fieldName}`
    if (field && resolvedTableId && !keys.has(key)) {
      keys.add(key)
      items.push({ field, tableId: resolvedTableId })
    }
  }
  for (const conversation of conversations.value) {
    for (const turn of [...conversation.turns].reverse()) {
      for (const field of turn.result.schema_graph?.fields ?? []) {
        if (field.source !== "relation_key") add(field.name, field.table_id)
      }
      for (const column of turn.result.columns) {
        add(column)
      }
    }
    for (const field of conversation.workspace.schema_fields ?? conversation.workspace.fields ?? []) {
      const tableId = field.tableId
      if (tableId) add(field.name, tableId)
    }
  }
  for (const table of historicalResultTables.value) {
    for (const column of table.columns) add(column)
  }

  return items.slice(0, FIELD_LIBRARY_LIMIT)
})
const savedFieldKeys = computed(() => new Set(
  savedMemories.value
    .filter((item) => item.kind === "schema_field")
    .map((item) => `${item.table_id}:${item.name}`),
))
const fieldLibrary = computed(() => {
  const items: { field: SchemaField; tableId: string; saved: boolean }[] = []
  const keys = new Set<string>()
  const add = (field: SchemaField, tableId: string, saved: boolean) => {
    const key = `${tableId}:${field.name}`
    if (keys.has(key)) return
    keys.add(key)
    items.push({ field, tableId, saved })
  }
  for (const memory of savedMemories.value) {
    if (memory.kind !== "schema_field" || !memory.table_id || !memory.name) continue
    const field = findField(memory.name, memory.table_id) ?? {
      name: memory.name,
      label: memory.label || memory.name,
      type: memory.field_type || "文本",
    }
    add(field, memory.table_id, true)
  }
  for (const item of recentFields.value) {
    add(item.field, item.tableId, savedFieldKeys.value.has(`${item.tableId}:${item.field.name}`))
  }
  return items.slice(0, FIELD_LIBRARY_LIMIT)
})
const fieldSourceMap = computed(() => {
  const sources = new Map<string, string>()
  const addResultColumnSource = (column: string, source: string) => {
    const table = schema.value.find((item) => item.fields.some((field) => field.name === column))
    if (table) sources.set(`${table.id}:${column}`, source)
  }
  for (const conversation of conversations.value) {
    for (const turn of conversation.turns) {
      for (const field of turn.result.schema_graph?.fields ?? []) {
        if (field.source === "relation_key") continue
        sources.set(`${field.table_id}:${field.name}`, turn.query)
      }
      for (const column of turn.result.columns) {
        addResultColumnSource(column, turn.query)
      }
    }
  }
  for (const table of historicalResultTables.value) {
    for (const column of table.columns) addResultColumnSource(column, table.query)
  }
  return sources
})
const contextSummary = computed(() => {
  const fieldCount = confirmedFields.value.length
  const resultCount = selectedAnalysisTables.value.length
  if (!fieldCount && !resultCount) return "暂未添加岗位字段或岗位结果"
  return `本次查询将使用 ${fieldCount} 个岗位字段 · ${resultCount} 个岗位结果`
})
const historyGroups = computed(() => {
  const today = new Date().toDateString()
  const recent: ConversationRecord[] = []
  const earlier: ConversationRecord[] = []
  for (const item of conversations.value) {
    if (new Date(item.updatedAt).toDateString() === today) recent.push(item)
    else earlier.push(item)
  }
  return [
    { label: "今天", items: recent },
    { label: "更早", items: earlier },
  ].filter((group) => group.items.length)
})
const userRoleLabel = computed(() => {
  if (authUser.value?.role === "local") return "本地岗位市场"
  if (authUser.value?.role === "admin") return "岗位市场管理员"
  if (authUser.value?.role === "analyst") return "演示岗位数据"
  return "岗位研究工作区"
})

onMounted(async () => {
  try {
    authUser.value = await api.me()
  } catch (caught) {
    api.clearSession()
    error.value = caught instanceof Error ? caught.message : "本地工作区加载失败"
  }
  authReady.value = true
  if (authUser.value) await initializeUserWorkspace()
})

async function initializeUserWorkspace() {
  conversations.value = []
  activeConversationId.value = ""
  workspace.value = {}
  schema.value = []
  savedMemories.value = []
  createConversation()
  try {
    const [schemaResult, memories] = await Promise.all([api.schema(), api.memories()])
    schema.value = schemaResult
    savedMemories.value = memories
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "工作区加载失败"
  }
}

function useMockAccount(username: string, password: string) {
  loginUsername.value = username
  loginPassword.value = password
  error.value = ""
}

async function loginUser() {
  if (!loginUsername.value.trim() || !loginPassword.value || loginLoading.value) return
  loginLoading.value = true
  error.value = ""
  try {
    authUser.value = await api.login(loginUsername.value.trim(), loginPassword.value)
    await initializeUserWorkspace()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "登录失败"
  } finally {
    loginLoading.value = false
  }
}

async function logoutUser() {
  if (loading.value) return
  try {
    await api.logout()
  } finally {
    authUser.value = null
    conversations.value = []
    activeConversationId.value = ""
    workspace.value = {}
    schema.value = []
    savedMemories.value = []
    input.value = ""
    error.value = ""
  }
}

function normalizeWorkspace(value?: WorkspaceConfig): WorkspaceConfig {
  const fields = value?.schema_fields ?? value?.fields ?? []
  return {
    schema_fields: Array.isArray(fields)
      ? fields.map((field) => ({ name: field.name, tableId: field.tableId, aggregation: "auto" as const }))
      : [],
    analysis_table_ids: Array.isArray(value?.analysis_table_ids)
      ? [...value.analysis_table_ids]
      : [],
  }
}

function tidyConversations() {
  conversations.value.sort((a, b) => b.updatedAt - a.updatedAt)
  conversations.value = conversations.value.slice(0, 30)
}

function createConversation() {
  if (loading.value) return
  const existingEmpty = conversations.value.find((item) => !item.turns.length)
  if (existingEmpty) {
    selectConversation(existingEmpty.id)
    return
  }
  const item: ConversationRecord = {
    id: crypto.randomUUID(),
    title: "新对话",
    updatedAt: Date.now(),
    turns: [],
    workspace: {},
  }
  conversations.value.unshift(item)
  activeConversationId.value = item.id
  workspace.value = {}
  input.value = ""
  error.value = ""
  leftOpen.value = false
  tidyConversations()
}

function selectConversation(id: string) {
  const item = conversations.value.find((conversation) => conversation.id === id)
  if (!item) return
  activeConversationId.value = id
  workspace.value = normalizeWorkspace(item.workspace)
  input.value = ""
  error.value = ""
  leftOpen.value = false
  nextTick(() => conversationScroll.value?.scrollTo({ top: conversationScroll.value.scrollHeight }))
}

function removeConversation(id: string) {
  conversations.value = conversations.value.filter((item) => item.id !== id)
  if (activeConversationId.value === id) {
    if (conversations.value[0]) selectConversation(conversations.value[0].id)
    else createConversation()
  }
  tidyConversations()
}

function saveActiveConversation() {
  const conversation = activeConversation.value
  if (!conversation) return
  conversation.workspace = normalizeWorkspace(workspace.value)
  conversation.updatedAt = Date.now()
  if (conversation.title === "新对话" && conversation.turns[0]) {
    conversation.title = conversation.turns[0].query.slice(0, 18)
  }
  tidyConversations()
}

async function submit(text?: string) {
  const query = (text ?? input.value).trim()
  if (!query || loading.value) return
  if (!activeConversation.value) createConversation()
  loading.value = true
  pendingQuery.value = query
  error.value = ""
  input.value = ""
  try {
    const requestWorkspace: WorkspaceConfig = {
      ...workspace.value,
      analysis_tables: selectedAnalysisTables.value.map((table) => ({
        task_id: table.taskId,
        title: table.title,
        query: table.query,
        columns: table.columns,
        rows: table.rows.slice(0, 50),
      })),
    }
    const result = await api.query(
      query,
      requestWorkspace,
      activeConversation.value?.id ?? "studio-demo",
    )
    activeConversation.value?.turns.push({ query, result })
    saveActiveConversation()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "查询失败"
    input.value = query
  } finally {
    loading.value = false
    pendingQuery.value = ""
  }
}

async function saveResult(result: QueryResult) {
  await api.save(result.task_id)
  result.saved = true
  savedMemories.value = await api.memories()
  saveActiveConversation()
}

async function toggleSavedField(field: SchemaField, tableId: string) {
  const memoryId = `field:${tableId}.${field.name}`
  if (savedFieldKeys.value.has(`${tableId}:${field.name}`)) {
    await api.deleteMemory(memoryId)
  } else {
    await api.saveField(tableId, field)
  }
  savedMemories.value = await api.memories()
}

function isSavedField(fieldName: string, tableId?: string) {
  return Boolean(tableId && savedFieldKeys.value.has(`${tableId}:${fieldName}`))
}

function toggleConfirmedFieldMemory(fieldName: string, tableId?: string) {
  const field = findField(fieldName, tableId)
  if (field && tableId) toggleSavedField(field, tableId)
}

function resetWorkspace() {
  workspace.value = {}
  saveActiveConversation()
}
function isConfirmedField(fieldName: string, tableId?: string) {
  return Boolean(tableId && confirmedFields.value.some(
    (field) => field.name === fieldName && field.tableId === tableId,
  ))
}

function fieldSource(fieldName: string, tableId?: string) {
  return fieldSourceMap.value.get(`${tableId}:${fieldName}`) ?? `数据表：${tableLabel(tableId)}`
}

function scrollToLibrary() {
  librarySection.value?.scrollIntoView({ behavior: "smooth", block: "start" })
}
function setDragTarget(target: ContextDropTarget) {
  dragTarget.value = target
}

function clearDragTarget() {
  dragTarget.value = null
}


function dragAnalysisTable(event: DragEvent, table: HistoricalResultTable) {
  setDragTarget("result")
  event.dataTransfer?.setData("analysis-result-table", table.taskId)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = "copy"
}

function addAnalysisTable(taskId: string) {
  if (!historicalResultTables.value.some((table) => table.taskId === taskId) || analysisTableIds.value.includes(taskId)) return
  workspace.value.analysis_table_ids = [...analysisTableIds.value, taskId]
  saveActiveConversation()
}

function dropAnalysisTable(event: DragEvent) {
  clearDragTarget()
  addAnalysisTable(event.dataTransfer?.getData("analysis-result-table") ?? "")
}

function removeAnalysisTable(taskId: string) {
  workspace.value.analysis_table_ids = analysisTableIds.value.filter((id) => id !== taskId)
  saveActiveConversation()
}

function dragField(event: DragEvent, field: SchemaField, tableId: string) {
  setDragTarget("field")
  event.dataTransfer?.setData("field", field.name)
  event.dataTransfer?.setData("field-table", tableId)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = "copy"
}

function findField(fieldName: string, tableId?: string) {
  const selectedTable = tableId ? schema.value.find((table) => table.id === tableId) : undefined
  return selectedTable?.fields.find((field) => field.name === fieldName)
    ?? schema.value.flatMap((table) => table.fields).find((field) => field.name === fieldName)
}

function isNumericField(fieldName: string, tableId?: string) {
  const field = findField(fieldName, tableId)
  return field?.type === "数值" || field?.type === "整数"
}

function addConfirmedField(fieldName: string, sourceTableId?: string) {
  const tableId = sourceTableId
  if (!tableId || !findField(fieldName, tableId)) return
  if (confirmedFields.value.some((field) => field.name === fieldName && field.tableId === tableId)) return
  workspace.value.schema_fields = [
    ...confirmedFields.value,
    {
      name: fieldName,
      aggregation: "auto",
      tableId,
    },
  ]
  saveActiveConversation()
}

function dropConfirmedField(event: DragEvent) {
  clearDragTarget()
  addConfirmedField(
    event.dataTransfer?.getData("field") ?? "",
    event.dataTransfer?.getData("field-table") ?? undefined,
  )
}

function removeConfirmedField(fieldName: string, tableId?: string) {
  workspace.value.schema_fields = confirmedFields.value.filter(
    (field) => !(field.name === fieldName && field.tableId === tableId),
  )
  saveActiveConversation()
}

function fieldLabel(fieldName: string, tableId?: string) {
  return findField(fieldName, tableId)?.label ?? fieldName
}

function tableLabel(tableId?: string) {
  return schema.value.find((table) => table.id === tableId)?.label ?? "未知数据表"
}

function clarificationHint(result: QueryResult) {
  return result.clarification?.options.map((option) => option.label).join("、") ?? ""
}

function handleComposerKeydown(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.isComposing || composing.value) return
  const commandEnter = (event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey
  const plainEnter = !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey
  const shouldSubmit = submitShortcut.value === "enter" ? plainEnter : commandEnter
  if (!shouldSubmit) return
  event.preventDefault()
  void submit()
}

function startComposition() {
  composing.value = true
}

function endComposition() {
  composing.value = false
}
</script>

<template>
  <div v-if="!authReady" class="auth-loading"><span></span><p>正在连接 OfferAgent…</p></div>

  <main v-else-if="!authUser" class="login-page">
    <section class="login-intro">
      <div class="login-brand"><span>O</span><strong>OfferAgent</strong></div>
      <div>
        <p class="kicker">AI CAREER DECISION ASSISTANT</p>
        <h1>让岗位数据，<br>服务于你的下一步。</h1>
        <p>连接岗位市场与个人简历，分析技能差距、薪资口径和面试准备方向。</p>
      </div>
      <small>ETL · QueryAgent · CareerAgent</small>
    </section>

    <section class="login-side">
      <form class="login-card" @submit.prevent="loginUser">
        <header><span class="login-mark">O</span><div><h2>欢迎回来</h2><p>登录 OfferAgent</p></div></header>
        <label>
          <span>账号</span>
          <input v-model="loginUsername" autocomplete="username" placeholder="请输入账号">
        </label>
        <label>
          <span>密码</span>
          <input v-model="loginPassword" type="password" autocomplete="current-password" placeholder="请输入密码">
        </label>
        <p v-if="error" class="login-error">{{ error }}</p>
        <button class="login-submit" :disabled="loginLoading" type="submit">
          {{ loginLoading ? "正在登录…" : "登录" }}
        </button>

        <div class="mock-accounts">
          <p>演示账号</p>
          <button type="button" @click="useMockAccount('admin', 'admin123')">
            <span class="account-icon admin">管</span>
            <span><strong>admin</strong><small>管理员 · 全部数据</small></span>
            <code>admin123</code>
          </button>
          <button type="button" @click="useMockAccount('sales', 'sales123')">
            <span class="account-icon user">销</span>
            <span><strong>sales</strong><small>岗位研究 · jobs_market</small></span>
            <code>sales123</code>
          </button>
          <button type="button" @click="useMockAccount('mock', 'mock123')">
            <span class="account-icon mock">示</span>
            <span><strong>mock</strong><small>演示岗位数据 · jobs_market</small></span>
            <code>mock123</code>
          </button>
        </div>
        <small class="login-note">本地演示账号仅用于开发和评测，不适合生产环境。</small>
      </form>
    </section>
  </main>

  <div v-else class="app-layout">
    <aside class="history-sidebar" :class="{ open: leftOpen }">
      <div class="brand-row">
        <span class="brand-symbol">O</span>
        <div><strong>OfferAgent</strong><small>求职决策助手</small></div>
      </div>

      <button class="new-chat" :disabled="loading || !activeConversation?.turns.length" @click="createConversation"><span>＋</span>新建对话</button>

      <nav class="history-list" aria-label="对话历史">
        <section v-for="group in historyGroups" :key="group.label">
          <p>{{ group.label }}</p>
          <div
            v-for="item in group.items"
            :key="item.id"
            class="history-item"
            :class="{ active: item.id === activeConversationId }"
          >
            <button class="history-select" @click="selectConversation(item.id)">
              <span class="chat-icon">◌</span>
              <span><strong>{{ item.title }}</strong><small>{{ item.turns.length }} 轮对话</small></span>
            </button>
            <button class="history-remove" aria-label="删除对话" @click="removeConversation(item.id)">×</button>
          </div>
        </section>
      </nav>

      <div class="history-footer">
        <div class="user-avatar">{{ authUser.display_name.slice(0, 1) }}</div>
        <div class="history-user"><strong>{{ authUser.display_name }}</strong><small><i></i> {{ userRoleLabel }}</small></div>
        <button class="logout-button" @click="logoutUser">退出</button>
      </div>
    </aside>

    <main class="chat-column">
      <header class="chat-header">
        <button class="mobile-menu" @click="leftOpen = !leftOpen">☰</button>
        <div>
          <strong>{{ activeConversation?.title ?? "新对话" }}</strong>
          <span>岗位市场与求职分析</span>
        </div>
        <button class="config-trigger" @click="rightOpen = !rightOpen">上下文设置</button>
        <span class="model-state"><i></i> 证据优先模式</span>
      </header>

      <section ref="conversationScroll" class="conversation-scroll">
        <Transition name="conversation" mode="out-in">
        <div :key="activeConversationId" class="conversation-view">
        <div v-if="!activeConversation?.turns.length && !loading" class="welcome-panel">
          <div class="welcome-copy">
            <span class="welcome-mark">✦</span>
            <p class="kicker">OFFERAGENT WORKSPACE</p>
            <h1>从岗位市场，找到下一步。</h1>
            <p>查询岗位趋势、薪资与技能要求，也可以结合简历证据获得求职建议。</p>
            <div class="prompt-list">
              <button v-for="prompt in prompts" :key="prompt" @click="submit(prompt)">
                <span>↗</span>{{ prompt }}
              </button>
            </div>
          </div>
        </div>

        <div v-else class="thread">
          <article v-for="(turn, turnIndex) in activeConversation?.turns" :key="`${turn.result.task_id}:${turnIndex}`" class="turn">
            <div class="user-message">
              <div class="message-avatar user">你</div>
              <div class="message-body"><small>你</small><p>{{ turn.query }}</p></div>
            </div>

            <div class="assistant-message">
              <div class="message-avatar assistant">O</div>
              <div class="message-body assistant-body">
                <div class="answer-heading" :class="{ 'qa-heading': turn.result.route !== 'database_query' }">
                  <div><small>OfferAgent</small><strong v-if="turn.result.route === 'database_query'">{{ turn.result.status === "failed" ? "处理失败" : turn.result.status === "waiting_clarification" ? "需要补充信息" : "岗位查询完成" }}</strong></div>
                  <span v-if="turn.result.route === 'database_query' && turn.result.status === 'completed'">
                    {{ turn.result.workflow_mode === "single_database_agent" ? "单库智能体" : "多库流程" }}
                  </span>
                </div>

                <ClarificationPanel v-if="turn.result.status === 'waiting_clarification'" :clarification="turn.result.clarification" @choose="submit" />

                <ExecutionTimeline :steps="turn.result.steps" :attempts="turn.result.attempts" :status="turn.result.status" />
                <details v-if="turn.result.retrieval && turn.result.status !== 'waiting_clarification'" class="execution-details">
                  <summary>查看检索过程</summary>
                  <div class="execution-strip">
                    <span>BM25 {{ turn.result.retrieval.bm25_count }} · Dense {{ turn.result.retrieval.dense_count }}</span>
                    <span>RRF {{ turn.result.retrieval.rrf_count }} → Rerank {{ turn.result.retrieval.selected_count }} 字段</span>
                    <span>阈值 ≥ {{ turn.result.retrieval.threshold.toFixed(2) }}</span>
                    <span v-if="turn.result.schema_graph">Schema 图 {{ turn.result.schema_graph.tables.length }} 表 · {{ turn.result.schema_graph.fields.length }} 字段</span>
                  </div>
                </details>

                <div
                  v-if="turn.result.standalone_query && turn.result.standalone_query !== turn.query"
                  class="standalone-query"
                >
                  <small>本轮独立查询</small>
                  <span>{{ turn.result.standalone_query }}</span>
                </div>

                <div v-if="turn.result.analysis_sources?.length" class="analysis-source-strip">
                  <strong>综合分析来源</strong>
                  <span v-for="source in turn.result.analysis_sources" :key="source.task_id">
                    ▦ {{ source.title }} · {{ source.row_count }} 行
                  </span>
                </div>

                <div v-if="turn.result.interpretation" class="scope-card">
                  <div class="scope-head"><strong>本次查询口径</strong><span>✓ 已确认</span></div>
                  <div class="scope-grid">
                    <div><small>数据表</small><strong>{{ turn.result.interpretation.table }}</strong></div>
                    <div><small>指标</small><strong>{{ turn.result.interpretation.metric }}</strong></div>
                    <div><small>维度</small><strong>{{ turn.result.interpretation.dimension }}</strong></div>
                    <div><small>时间</small><strong>{{ turn.result.interpretation.time_range }}</strong></div>
                  </div>
                </div>

                <ReportCard
                  v-if="turn.result.report"
                  :report="turn.result.report"
                  :sources="reportDataSources"
                />

                <p v-else-if="turn.result.route !== 'database_query' && turn.result.analysis" class="qa-answer">{{ turn.result.analysis }}</p>

                <ResultTableCard
                  v-if="turn.result.route === 'database_query' && turn.result.status === 'completed' && turn.result.rows.length"
                  :title="turn.result.result_title || '查询结果'"
                  :columns="turn.result.columns"
                  :rows="turn.result.rows"
                >
                  <template #actions>
                      <button v-if="turn.result.sql" @click="expandedSql = expandedSql === turn.result.task_id ? null : turn.result.task_id">SQL</button>
                      <button class="save-result" :disabled="turn.result.saved" @click="saveResult(turn.result)">{{ turn.result.saved ? "已保存" : "保存" }}</button>
                  </template>
                  <template #details>
                    <pre v-if="expandedSql === turn.result.task_id && turn.result.sql"><code>{{ turn.result.sql }}</code></pre>
                  </template>
                </ResultTableCard>

                <div
                  v-if="turn.result.route === 'database_query' && turn.result.analysis && turn.result.status !== 'waiting_clarification'"
                  class="result-analysis"
                >
                  <strong>结果说明</strong>
                  <p>{{ turn.result.analysis }}</p>
                </div>
              </div>
            </div>
          </article>

          <div v-if="loading && pendingQuery" class="user-message pending-user-message">
            <div class="message-avatar user">你</div>
            <div class="message-body"><small>你 · 已发送</small><p>{{ pendingQuery }}</p></div>
          </div>

          <div v-if="loading" class="assistant-message loading-message">
            <div class="message-avatar assistant">O</div>
            <div class="loading-copy"><span></span><div><strong>正在分析</strong><small>提取岗位口径并核对岗位数据…</small></div></div>
          </div>
        </div>
        </div>
        </Transition>

        <div v-if="error" class="error-banner">{{ error }}</div>
      </section>

      <footer class="composer">
        <div class="composer-box">
          <textarea
            v-model="input"
            rows="1"
            placeholder="输入岗位市场或求职分析问题…"
            @keydown="handleComposerKeydown"
            @compositionstart="startComposition"
            @compositionend="endComposition"
          ></textarea>
          <div class="composer-meta">
            <span>上下文：{{ confirmedFields.length }} 个岗位字段 · {{ selectedAnalysisTables.length }} 个岗位结果</span>
            <div class="composer-actions">
              <label class="shortcut-control">
                <span>提交方式</span>
                <select v-model="submitShortcut" :disabled="loading" aria-label="选择提交快捷键">
                  <option value="enter">按 Enter 提交</option>
                  <option value="metaEnter">按 ⌘+Enter 提交</option>
                </select>
              </label>
              <button class="send-button" :disabled="!input.trim() || loading" aria-label="发送求职问题" @click="submit()">↑</button>
            </div>
          </div>
        </div>
        <small>建议提供岗位关键词和城市；缺少目标岗位或简历上下文时，系统会继续询问。</small>
      </footer>
    </main>

    <aside class="query-panel" :class="{ open: rightOpen }">
      <div class="panel-header">
        <div><p class="kicker">CAREER CONTEXT</p><h2>求职上下文</h2></div>
        <button @click="resetWorkspace">清空当前上下文</button>
      </div>
      <DemoTaskPanel />
      <p class="context-intro">已加入的岗位字段和岗位结果会用于下一条查询。你可以点击下方“添加到分析上下文”，也可以拖拽添加。</p>
      <div class="context-summary">{{ contextSummary }}</div>

      <section class="context-block query-context-block">
        <div class="context-heading">
          <div><strong>岗位字段</strong><small>用于下一条查询的筛选条件和分析维度</small></div>
          <em>{{ confirmedFields.length }} 个</em>
        </div>
        <div
          class="confirmed-fields-zone"
          :class="{ filled: confirmedFields.length, 'drop-ready': dragTarget === 'field' }"
          @dragover.prevent="setDragTarget('field')"
          @dragleave.self="clearDragTarget"
          @drop.prevent="dropConfirmedField"
        >
          <div v-if="!confirmedFields.length" class="empty-confirmation">
            <span>＋</span>
            <strong>还没有添加岗位字段</strong>
            <small>字段来自下方的岗位字段列表</small>
            <button class="empty-action" @click="scrollToLibrary">去下方添加</button>
          </div>
          <div v-for="field in confirmedFields" :key="`${field.tableId}:${field.name}`" class="confirmed-field">
            <span class="field-badge">{{ isNumericField(field.name, field.tableId) ? "#" : "T" }}</span>
            <span class="confirmed-field-name"><strong>{{ fieldLabel(field.name, field.tableId) }}</strong><small>来源：{{ fieldSource(field.name, field.tableId) }}</small></span>
            <button class="save-field" :class="{ saved: isSavedField(field.name, field.tableId) }" :aria-label="`保存${fieldLabel(field.name, field.tableId)}`" @click="toggleConfirmedFieldMemory(field.name, field.tableId)">{{ isSavedField(field.name, field.tableId) ? "★" : "☆" }}</button>
            <button class="remove-field" :aria-label="`移除${fieldLabel(field.name, field.tableId)}`" @click="removeConfirmedField(field.name, field.tableId)">×</button>
          </div>
        </div>
      </section>

      <section class="context-block analysis-context-block">
        <div class="context-heading">
          <div><strong>岗位结果</strong><small>用于下一条问题的对比和综合分析</small></div>
          <em>{{ selectedAnalysisTables.length }} 张</em>
        </div>
        <div
          class="analysis-tables-zone"
          :class="{ filled: selectedAnalysisTables.length, 'drop-ready': dragTarget === 'result' }"
          @dragover.prevent="setDragTarget('result')"
          @dragleave.self="clearDragTarget"
          @drop.prevent="dropAnalysisTable"
        >
          <div v-if="!selectedAnalysisTables.length" class="empty-confirmation">
            <span>＋</span>
            <strong>还没有添加岗位结果</strong>
            <small>结果来自已保存的岗位查询</small>
            <button class="empty-action" @click="scrollToLibrary">去下方添加</button>
          </div>
          <div v-for="table in selectedAnalysisTables" :key="table.taskId" class="analysis-table-chip">
            <span class="table-mark">▦</span>
            <span><strong>{{ table.title }}</strong><small>{{ table.rowCount }} 行 · 来源：{{ table.query }}</small></span>
            <button :aria-label="`移除${table.title}`" @click="removeAnalysisTable(table.taskId)">×</button>
          </div>
        </div>
      </section>

      <section ref="librarySection" class="recent-library">
        <div class="library-heading">
          <div><strong>岗位字段与结果</strong><small>这里是查询后保存的资料，点击按钮即可添加到上方上下文</small></div>
        </div>
        <div class="recent-columns">
          <div class="recent-column">
            <p>可添加的岗位字段 · 最多 {{ FIELD_LIBRARY_LIMIT }} 个</p>
            <div v-if="!fieldLibrary.length" class="library-empty">先查询岗位数据，字段会显示在这里</div>
            <div
              v-for="item in fieldLibrary"
              :key="`${item.tableId}:${item.field.name}`"
              class="field-library-row"
              draggable="true"
              @dragstart="dragField($event, item.field, item.tableId)"
              @dragend="clearDragTarget"
            >
              <div
                class="recent-card field-card"
                role="button"
                tabindex="0"
                :aria-label="`${item.field.label}，来源：${fieldSource(item.field.name, item.tableId)}`"
                @dblclick="addConfirmedField(item.field.name, item.tableId)"
                @keydown.enter.prevent="addConfirmedField(item.field.name, item.tableId)"
                @keydown.space.prevent="addConfirmedField(item.field.name, item.tableId)"
              >
                <span class="field-badge">{{ item.field.type === "数值" || item.field.type === "整数" ? "#" : "T" }}</span>
                <span><strong>{{ item.field.label }}</strong><small>来源：{{ fieldSource(item.field.name, item.tableId) }}</small></span>
              </div>
              <div class="library-row-actions">
                <button class="context-add-button" :class="{ added: isConfirmedField(item.field.name, item.tableId) }" :disabled="isConfirmedField(item.field.name, item.tableId)" @click="addConfirmedField(item.field.name, item.tableId)">{{ isConfirmedField(item.field.name, item.tableId) ? "已添加" : "添加到上下文" }}</button>
                <button class="save-field" :class="{ saved: item.saved }" :aria-label="`${item.saved ? '取消收藏' : '收藏'}${item.field.label}`" @click="toggleSavedField(item.field, item.tableId)">{{ item.saved ? "★" : "☆" }}</button>
              </div>
            </div>
          </div>
          <div class="recent-column">
            <p>可添加的岗位结果 · 最多 {{ SAVED_TABLE_LIMIT }} 张</p>
            <div v-if="!historicalResultTables.length" class="library-empty">先保存岗位查询结果，结果会显示在这里</div>
            <div
              v-for="table in historicalResultTables"
              :key="table.taskId"
              class="table-library-row"
              draggable="true"
              @dragstart="dragAnalysisTable($event, table)"
              @dragend="clearDragTarget"
            >
              <div
                class="recent-card table-card"
                role="button"
                tabindex="0"
                :aria-label="`${table.title}，来源：${table.query}`"
                @dblclick="addAnalysisTable(table.taskId)"
                @keydown.enter.prevent="addAnalysisTable(table.taskId)"
                @keydown.space.prevent="addAnalysisTable(table.taskId)"
              >
                <span class="table-mark">▦</span>
                <span><strong>{{ table.title }}</strong><small>{{ table.rowCount }} 行 · 来源：{{ table.query }}</small></span>
                <em>⋮⋮</em>
              </div>
              <button class="context-add-button" :class="{ added: analysisTableIds.includes(table.taskId) }" :disabled="analysisTableIds.includes(table.taskId)" @click="addAnalysisTable(table.taskId)">{{ analysisTableIds.includes(table.taskId) ? "已添加" : "添加到上下文" }}</button>
            </div>
          </div>
        </div>
      </section>
    </aside>

  </div>
</template>
