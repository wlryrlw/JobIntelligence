<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue"
import { api } from "./api"
import CareerReportDialog from "./components/CareerReportDialog.vue"
import ClarificationPanel from "./components/ClarificationPanel.vue"
import CrawlPanel from "./components/CrawlPanel.vue"
import ExecutionTimeline from "./components/ExecutionTimeline.vue"
import MarkdownReport from "./components/MarkdownReport.vue"
import ReportCard from "./components/ReportCard.vue"
import ResultTableCard from "./components/ResultTableCard.vue"
import type {
  AuthUser,
  CareerReport,
  CellValueMap,
  QueryProgressEvent,
  QueryResult,
  ReportDataSource,
  ResumeUploadResponse,
  SavedMemory,
  WorkspaceConfig,
} from "./types"

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
  columnLabels: Record<string, string>
  rows: CellValueMap[]
}

type SubmitShortcut = "enter" | "metaEnter"
type ContextDropTarget = "result" | null

const SAVED_TABLE_LIMIT = 8
const RESUME_SESSION_KEY = "jobintel_resume"
const MAX_RESUME_BYTES = 5 * 1024 * 1024
const ACCEPTED_RESUME_EXTENSIONS = ["txt", "text", "md", "markdown", "pdf"]
const prompts = [
  "目前AI agent岗位的常见技能有哪些",
  "统计python岗位的薪资分布",
  "对比上海和杭州的AI岗位要求",
]

const input = ref("")
const loading = ref(false)
const pendingQuery = ref("")
const progress = ref({ stage: "started", message: "正在准备问答", elapsedMs: 0 })
const error = ref("")
const savedActionError = ref("")
const savedActionMessage = ref("")
const savedMemories = ref<SavedMemory[]>([])
const workspace = ref<WorkspaceConfig>({})
const useJdRag = ref(false)
const conversations = ref<ConversationRecord[]>([])
const activeConversationId = ref("")
const expandedSql = ref<string | null>(null)
const leftOpen = ref(false)
const rightOpen = ref(false)
const conversationScroll = ref<HTMLElement | null>(null)
const conversationEnd = ref<HTMLElement | null>(null)
const shouldFollowConversation = ref(true)
let conversationScrollFrame: number | null = null
const authUser = ref<AuthUser | null>(null)
const authReady = ref(false)
const loginLoading = ref(false)
const loginUsername = ref("admin")
const loginPassword = ref("admin123")
const submitShortcut = ref<SubmitShortcut>("enter")
const dragTarget = ref<ContextDropTarget>(null)
const composing = ref(false)

const resumeInput = ref<HTMLInputElement | null>(null)
const resume = ref<ResumeUploadResponse | null>(null)
const resumeUploading = ref(false)
const resumeError = ref("")
const resumePanelOpen = ref(false)
const careerDialogOpen = ref(false)
const careerReport = ref<CareerReport | null>(null)

const activeConversation = computed(() =>
  conversations.value.find((item) => item.id === activeConversationId.value),
)
const activeCrawlRunId = computed(() => {
  const turns = activeConversation.value?.turns ?? []
  return [...turns].reverse().find((turn) => turn.result.crawl_run_id)?.result.crawl_run_id ?? null
})
const analysisTableIds = computed(() => workspace.value.analysis_table_ids ?? [])
const historicalResultTables = computed<HistoricalResultTable[]>(() => {
  return savedMemories.value
    .filter((item) => item.kind === "result_table" && item.task_id)
    .map((item) => ({
      taskId: item.task_id!,
      title: item.title || "查询结果",
      query: item.query || "",
      rowCount: item.rows?.length || 0,
      columns: item.columns || [],
      columnLabels: item.column_labels || {},
      rows: item.rows || [],
    }))
    .slice(0, SAVED_TABLE_LIMIT)
})
const selectedAnalysisTables = computed(() =>
  analysisTableIds.value
    .map((id) => historicalResultTables.value.find((table) => table.taskId === id))
    .filter((table): table is HistoricalResultTable => Boolean(table)),
)
const reportDataSources = computed<ReportDataSource[]>(() => {
  const sources = new Map<string, ReportDataSource>()
  for (const conversation of conversations.value) {
    for (const turn of conversation.turns) {
      if (!turn.result.rows.length) continue
      sources.set(turn.result.task_id, {
        taskId: turn.result.task_id,
        title: turn.result.result_title || "查询结果",
        columns: turn.result.columns,
        columnLabels: turn.result.column_labels,
        rows: turn.result.rows,
      })
    }
  }
  for (const table of historicalResultTables.value) {
    sources.set(table.taskId, {
      taskId: table.taskId,
      title: table.title,
      columns: table.columns,
      columnLabels: table.columnLabels,
      rows: table.rows,
    })
  }
  return [...sources.values()]
})
const contextSummary = computed(() => {
  const count = selectedAnalysisTables.value.length
  return count ? `下一次查询将参考 ${count} 个已保存的岗位结果` : "暂未添加岗位结果；普通查询无需手动添加"
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
  if (authUser.value?.role === "candidate") return "求职分析工作区"
  return "岗位研究工作区"
})

onMounted(async () => {
  restoreResume()
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
  savedMemories.value = []
  createConversation()
  try {
    savedMemories.value = await api.memories()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "工作区加载失败"
  }
}

function restoreResume() {
  const raw = sessionStorage.getItem(RESUME_SESSION_KEY)
  if (!raw) return
  try {
    resume.value = JSON.parse(raw) as ResumeUploadResponse
    resumePanelOpen.value = true
  } catch {
    sessionStorage.removeItem(RESUME_SESSION_KEY)
  }
}

function useDemoAccount(username: string, password: string) {
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
    savedMemories.value = []
    resume.value = null
    sessionStorage.removeItem(RESUME_SESSION_KEY)
    input.value = ""
    error.value = ""
  }
}

function normalizeWorkspace(value?: WorkspaceConfig): WorkspaceConfig {
  return {
    analysis_table_ids: Array.isArray(value?.analysis_table_ids) ? [...value.analysis_table_ids] : [],
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
  scrollConversationToBottom({ force: true })
}

function handleConversationScroll() {
  const element = conversationScroll.value
  if (!element) return
  const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight
  shouldFollowConversation.value = distanceFromBottom <= 96
}

function scrollConversationToBottom({
  force = false,
}: { force?: boolean } = {}) {
  if (force) shouldFollowConversation.value = true
  if (!force && !shouldFollowConversation.value) return

  if (conversationScrollFrame !== null) cancelAnimationFrame(conversationScrollFrame)
  nextTick(() => {
    conversationScrollFrame = requestAnimationFrame(() => {
      const element = conversationScroll.value
      if (!element || (!force && !shouldFollowConversation.value)) {
        conversationScrollFrame = null
        return
      }
      conversationEnd.value?.scrollIntoView({ behavior: "auto", block: "end" })
      element.scrollTop = element.scrollHeight
      // 结果表格、报告卡片等子组件可能在下一帧才完成布局，再校准一次位置。
      conversationScrollFrame = requestAnimationFrame(() => {
        if (force || shouldFollowConversation.value) {
          element.scrollTop = element.scrollHeight
        }
        conversationScrollFrame = null
      })
    })
  })
}

onBeforeUnmount(() => {
  if (conversationScrollFrame !== null) cancelAnimationFrame(conversationScrollFrame)
})

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
  if (conversation.title === "新对话" && conversation.turns[0]) conversation.title = conversation.turns[0].query.slice(0, 18)
  tidyConversations()
}

async function submit(text?: string) {
  const query = (text ?? input.value).trim()
  if (!query || loading.value) return
  if (!activeConversation.value) createConversation()
  loading.value = true
  pendingQuery.value = query
  progress.value = { stage: "started", message: "正在准备问答", elapsedMs: 0 }
  error.value = ""
  input.value = ""
  // 发送新问题是用户主动开始的新一轮对话，确保先显示待处理消息。
  scrollConversationToBottom({ force: true })
  const taskId = crypto.randomUUID()
  const progressController = new AbortController()
  const progressPromise = api.streamQueryEvents(
    taskId,
    (event: QueryProgressEvent) => {
      const payload = event.payload
      progress.value = {
        stage: payload.stage || event.event_type,
        message: payload.message || "正在处理问题",
        elapsedMs: typeof payload.elapsed_ms === "number" ? payload.elapsed_ms : progress.value.elapsedMs,
      }
    },
    progressController.signal,
  ).catch(() => undefined)
  try {
    const requestWorkspace: WorkspaceConfig = {
      ...workspace.value,
      analysis_tables: selectedAnalysisTables.value.map((table) => ({
        task_id: table.taskId,
        title: table.title,
        query: table.query,
        columns: table.columns,
        column_labels: table.columnLabels,
        rows: table.rows.slice(0, 50),
      })),
    }
    const result = await api.query(
      query,
      requestWorkspace,
      activeConversation.value?.id ?? "studio-demo",
      resume.value ? { resume_id: resume.value.resume_id } : undefined,
      taskId,
      useJdRag.value,
    )
    activeConversation.value?.turns.push({ query, result })
    saveActiveConversation()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "查询失败"
    input.value = query
  } finally {
    progressController.abort()
    // 进度 SSE 只是旁路观测，不能阻塞最终 QueryResult 的展示。
    loading.value = false
    pendingQuery.value = ""
    scrollConversationToBottom()
  }
}

async function chooseClarification(result: QueryResult, optionId: string) {
  const option = result.clarification?.options.find((item) => item.id === optionId)
  const query = option?.label || optionId
  if (!option || loading.value || !activeConversation.value) return

  loading.value = true
  pendingQuery.value = query
  progress.value = { stage: "clarification", message: "正在应用你的选择并继续分析", elapsedMs: 0 }
  error.value = ""
  scrollConversationToBottom({ force: true })
  try {
    const nextResult = await api.clarify(result.task_id, optionId)
    activeConversation.value.turns.push({ query, result: nextResult })
    saveActiveConversation()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "无法应用选择"
    input.value = query
  } finally {
    loading.value = false
    pendingQuery.value = ""
    scrollConversationToBottom()
  }
}

function progressStageLabel(stage: string) {
  const labels: Record<string, string> = {
    started: "启动",
    workflow: "流程协调",
    request_preprocessing: "请求理解与路由",
    route_selected: "处理路径已选择",
    schema_catalog: "读取岗位数据结构",
    sql_generation: "生成只读 SQL",
    sql_validation: "校验 SQL 安全性",
    sql_execution: "执行岗位数据查询",
    data_qa: "分析已有岗位结果",
    result_analysis: "整理查询结果",
    direct_response: "整理回复",
    career_resource_gate: "检查求职分析资源",
    career_resume: "读取简历证据",
    career_profile: "构建岗位画像",
    career_analysis: "生成求职分析",
    career_rag: "检索岗位 JD 证据",
    completed: "已完成",
    query_failed: "查询失败",
    clarification: "等待补充信息",
    timeout: "已超时",
  }
  return labels[stage] || stage
}

async function saveResult(result: QueryResult) {
  try {
    await api.save(result.task_id)
    result.saved = true
    savedMemories.value = await api.memories()
    saveActiveConversation()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "保存查询结果失败"
  }
}

async function deleteSavedResult(table: HistoricalResultTable) {
  if (!confirm(`确定删除“${table.title}”吗？删除后不能再作为后续分析上下文。`)) return
  savedActionError.value = ""
  savedActionMessage.value = ""
  try {
    await api.deleteMemory(`result:${table.taskId}`)
    workspace.value.analysis_table_ids = analysisTableIds.value.filter((id) => id !== table.taskId)
    savedMemories.value = await api.memories()
    savedActionMessage.value = "已删除保存结果，并从当前分析上下文移除。"
    window.setTimeout(() => { savedActionMessage.value = "" }, 3200)
    saveActiveConversation()
  } catch (caught) {
    savedActionError.value = caught instanceof Error ? caught.message : "删除岗位结果失败"
  }
}

function resetWorkspace() {
  workspace.value = { analysis_table_ids: [] }
  saveActiveConversation()
}

function scrollToLibrary() {
  rightOpen.value = true
  nextTick(() => document.querySelector(".result-library")?.scrollIntoView({ behavior: "smooth", block: "start" }))
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

function handleComposerKeydown(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.isComposing || composing.value) return
  const commandEnter = (event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey
  const plainEnter = !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey
  const shouldSubmit = submitShortcut.value === "enter" ? plainEnter : commandEnter
  if (!shouldSubmit) return
  event.preventDefault()
  void submit()
}

function startComposition() { composing.value = true }
function endComposition() { composing.value = false }

function openResumePicker() {
  resumeInput.value?.click()
}

function openResumePanel() {
  resumePanelOpen.value = true
}

async function handleResumeSelected(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  resumeError.value = ""
  const extension = file.name.split(".").pop()?.toLowerCase() || ""
  if (!ACCEPTED_RESUME_EXTENSIONS.includes(extension)) {
    resumeError.value = "文件格式不支持。请上传 UTF-8 编码的 TXT、TEXT、MD、MARKDOWN，或可复制文本的 PDF；暂不支持 DOC/DOCX、图片或扫描 PDF。"
    if (resumeInput.value) resumeInput.value.value = ""
    return
  }
  if (file.size > MAX_RESUME_BYTES) {
    resumeError.value = "简历文件不能超过 5 MB，请压缩图片或导出为文本型 PDF 后重试。"
    if (resumeInput.value) resumeInput.value.value = ""
    return
  }
  resumeUploading.value = true
  try {
    const uploaded = await api.uploadResume(file)
    resume.value = uploaded
    sessionStorage.setItem(RESUME_SESSION_KEY, JSON.stringify(uploaded))
  } catch (caught) {
    resumeError.value = caught instanceof Error ? caught.message : "简历上传失败，请检查文件后重试。"
  } finally {
    resumeUploading.value = false
    if (resumeInput.value) resumeInput.value.value = ""
  }
}

async function removeResume() {
  if (!resume.value || resumeUploading.value) return
  resumeError.value = ""
  try {
    await api.deleteResume(resume.value.resume_id)
    resume.value = null
    sessionStorage.removeItem(RESUME_SESSION_KEY)
    careerReport.value = null
  } catch (caught) {
    resumeError.value = caught instanceof Error ? caught.message : "简历删除失败，请稍后重试。"
  }
}

function showCareerReport(report: CareerReport) {
  careerReport.value = report
  careerDialogOpen.value = true
}

function downloadCareerReport() {
  const report = careerReport.value
  if (!report) return
  const lines = [
    `# ${report.title}`,
    "",
    `- 目标岗位：${report.target.keyword}`,
    `- 城市：${report.target.city || "未限定"}`,
    `- 报告 ID：${report.report_id}`,
    "",
    "## 分析结论",
    report.summary,
    "",
    "## 口径与事实",
    ...report.facts.map((item) => `- ${item}`),
    "",
    "## 技能覆盖",
    ...report.skill_coverage.map((item) => `- ${item.skill}：${item.status === "covered" ? "已覆盖" : item.status === "partial" ? "部分覆盖" : "待补足"}；岗位出现率 ${item.jd_occurrence_rate == null ? "—" : `${(item.jd_occurrence_rate * 100).toFixed(0)}%`}`),
  ]
  const blob = new Blob(["\ufeff", lines.join("\n")], { type: "text/markdown;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = `${report.title.replace(/[\\/:*?"<>|]/g, "_")}.md`
  anchor.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div v-if="!authReady" class="auth-loading"><span></span><p>正在连接 JobIntel…</p></div>

  <main v-else-if="!authUser" class="login-page">
    <section class="login-intro">
      <div class="login-brand"><strong>JobIntel</strong></div>
      <div><p class="kicker">AI CAREER DECISION ASSISTANT</p><h1>让岗位数据，<br>服务于你的下一步。</h1><p>连接岗位市场与个人简历，分析技能差距、薪资口径和面试准备方向。</p></div>
      <small>ETL · QueryAgent · CareerAgent</small>
    </section>
    <section class="login-side">
      <form class="login-card" @submit.prevent="loginUser">
        <header><div><h2>欢迎回来</h2><p>登录 JobIntel</p></div></header>
        <label><span>账号</span><input v-model="loginUsername" autocomplete="username" placeholder="请输入账号"></label>
        <label><span>密码</span><input v-model="loginPassword" type="password" autocomplete="current-password" placeholder="请输入密码"></label>
        <p v-if="error" class="login-error">{{ error }}</p>
        <button class="login-submit" :disabled="loginLoading" type="submit">{{ loginLoading ? "正在登录…" : "登录" }}</button>
        <div class="demo-accounts"><p>演示账号</p><button type="button" @click="useDemoAccount('admin', 'admin123')"><span class="account-icon admin">管</span><span><strong>admin</strong><small>管理员 · 全部岗位数据</small></span><code>admin123</code></button><button type="button" @click="useDemoAccount('candidate', 'candidate123')"><span class="account-icon candidate">求</span><span><strong>candidate</strong><small>求职分析 · jobs_market</small></span><code>candidate123</code></button></div>
        <small class="login-note">本地演示账号仅用于开发和评测，不适合生产环境。</small>
      </form>
    </section>
  </main>

  <div v-else class="app-layout">
    <aside class="history-sidebar" :class="{ open: leftOpen }">
      <div class="brand-row"><div><strong>JobIntel</strong><small>求职决策助手</small></div></div>
      <button class="new-chat" :disabled="loading || !activeConversation?.turns.length" @click="createConversation"><span>＋</span>新建对话</button>
      <div class="sidebar-history-scroll">
        <nav class="history-list" aria-label="对话历史"><section v-for="group in historyGroups" :key="group.label"><p>{{ group.label }}</p><div v-for="item in group.items" :key="item.id" class="history-item" :class="{ active: item.id === activeConversationId }"><button class="history-select" @click="selectConversation(item.id)"><span class="chat-icon">◌</span><span><strong>{{ item.title }}</strong><small>{{ item.turns.length }} 轮对话</small></span></button><button class="history-remove" aria-label="删除对话" @click="removeConversation(item.id)">×</button></div></section></nav>
      </div>
      <CrawlPanel :initial-run-id="activeCrawlRunId" />
      <div class="history-footer"><div class="user-avatar">{{ authUser.display_name.slice(0, 1) }}</div><div class="history-user"><strong>{{ authUser.display_name }}</strong><small><i></i> {{ userRoleLabel }}</small></div><button class="logout-button" @click="logoutUser">退出</button></div>
    </aside>

    <main class="chat-column">
      <header class="chat-header"><button class="mobile-menu" @click="leftOpen = !leftOpen">☰</button><div><strong>{{ activeConversation?.title ?? "新对话" }}</strong><span>岗位市场与求职分析</span></div><button class="config-trigger" @click="rightOpen = !rightOpen">分析工具</button></header>
      <section ref="conversationScroll" class="conversation-scroll" @scroll="handleConversationScroll"><Transition name="conversation" mode="out-in"><div :key="activeConversationId" class="conversation-view">
        <div v-if="!activeConversation?.turns.length && !loading" class="welcome-panel"><div class="welcome-copy"><p>查询岗位趋势、薪资与技能要求，也可以结合简历证据获得求职建议。</p><div class="prompt-list"><button v-for="prompt in prompts" :key="prompt" @click="submit(prompt)"><span>↗</span>{{ prompt }}</button></div></div></div>
        <div v-else class="thread">
          <article v-for="(turn, turnIndex) in activeConversation?.turns" :key="`${turn.result.task_id}:${turnIndex}`" class="turn">
            <div class="user-message"><div class="message-avatar user">你</div><div class="message-body"><small>你</small><p>{{ turn.query }}</p></div></div>
            <div class="assistant-message"><div class="message-avatar assistant">AI</div><div class="message-body assistant-body">
              <div class="answer-heading" :class="{ 'qa-heading': turn.result.route !== 'database_query' }"><div><small>JobIntel</small><strong v-if="turn.result.route === 'database_query'">{{ turn.result.status === "failed" ? "处理失败" : turn.result.status === "waiting_clarification" ? "需要补充信息" : "岗位查询完成" }}</strong><strong v-else>{{ turn.result.route === "career_advice" ? "简历与岗位分析" : turn.result.route === "market_data_refresh" ? (turn.result.status === "waiting_clarification" ? "采集前确认" : "采集任务已创建") : turn.result.route === "direct_response" ? "JobIntel 回复" : "岗位分析完成" }}</strong></div><span v-if="turn.result.route === 'database_query' && turn.result.status === 'completed'">{{ turn.result.workflow_mode === "query_agent" ? "QueryAgent" : "多库流程" }}</span></div>
              <ClarificationPanel v-if="turn.result.status === 'waiting_clarification'" :clarification="turn.result.clarification" @choose="chooseClarification(turn.result, $event)" />
              <ExecutionTimeline :steps="turn.result.steps" :attempts="turn.result.attempts" :status="turn.result.status" />
              <div v-if="turn.result.error_code === 'timeout'" class="timeout-notice">本次问答耗时 {{ ((turn.result.elapsed_ms || 0) / 1000).toFixed(1) }} 秒，在“{{ progressStageLabel(turn.result.failure_stage || 'timeout') }}”阶段超时，已停止继续等待。</div>
              <div v-if="turn.result.analysis_sources?.length" class="analysis-source-strip"><strong>综合分析来源</strong><span v-for="source in turn.result.analysis_sources" :key="source.task_id">▦ {{ source.title }} · {{ source.row_count }} 行</span></div>
              <div v-if="turn.result.interpretation" class="scope-card"><div class="scope-head"><strong>本次查询口径</strong><span>✓ 已确认</span></div><div class="scope-grid"><div><small>数据表</small><strong>{{ turn.result.interpretation.table }}</strong></div><div><small>指标</small><strong>{{ turn.result.interpretation.metric }}</strong></div><div><small>维度</small><strong>{{ turn.result.interpretation.dimension }}</strong></div><div><small>时间</small><strong>{{ turn.result.interpretation.time_range }}</strong></div></div></div>
              <ReportCard v-if="turn.result.report" :report="turn.result.report" :sources="reportDataSources" />
              <div v-if="turn.result.career_report" class="career-result-card"><strong>{{ turn.result.career_report.title }}</strong><MarkdownReport :markdown="turn.result.career_report.summary" /><button @click="showCareerReport(turn.result.career_report)">查看完整分析报告</button></div>
              <div v-if="turn.result.rag_used" class="rag-source-strip">已检索岗位 JD 证据 · 数据集 {{ turn.result.rag_index_version || "当前版本" }} · {{ turn.result.rag_sources?.length || 0 }} 段</div>
              <MarkdownReport v-if="turn.result.route !== 'database_query' && turn.result.analysis" class="qa-answer" :markdown="turn.result.analysis" />
              <ResultTableCard v-if="turn.result.route === 'database_query' && turn.result.status === 'completed' && turn.result.rows.length" :title="turn.result.result_title || '查询结果'" :columns="turn.result.columns" :column-labels="turn.result.column_labels" :rows="turn.result.rows">
                <template #actions><button class="result-action" @click="expandedSql = expandedSql === turn.result.task_id ? null : turn.result.task_id">{{ expandedSql === turn.result.task_id ? "收起 SQL" : "查看 SQL" }}</button><button class="result-action" :class="{ saved: turn.result.saved }" @click="saveResult(turn.result)">{{ turn.result.saved ? "已保存" : "保存结果" }}</button></template>
                <template #details><div v-if="expandedSql === turn.result.task_id && turn.result.sql" class="result-card"><pre>{{ turn.result.sql }}</pre></div></template>
              </ResultTableCard>
              <div v-if="turn.result.route === 'database_query' && turn.result.status !== 'waiting_clarification' && (turn.result.analysis || turn.result.message)" class="result-analysis" :class="{ 'empty-result': !turn.result.rows.length && !turn.result.analysis }"><strong>{{ turn.result.analysis ? "结果解读" : "查询结果" }}</strong><MarkdownReport :markdown='turn.result.analysis || turn.result.message || "查询已完成，但没有返回可展示的数据。"' /></div>
            </div></div>
          </article>
          <div v-if="loading && pendingQuery" class="user-message pending-user-message"><div class="message-avatar user">你</div><div class="message-body"><small>你</small><p>{{ pendingQuery }}</p></div></div>
          <div v-if="loading" class="assistant-message loading-message"><div class="message-avatar assistant">AI</div><div class="loading-copy"><strong>{{ progress.message }}</strong><small>{{ progressStageLabel(progress.stage) }} · 已用时 {{ (progress.elapsedMs / 1000).toFixed(1) }} 秒</small></div></div>
        </div>
        <div ref="conversationEnd" class="conversation-end" aria-hidden="true"></div>
      </div></Transition></section>
      <div v-if="error" class="error-banner">{{ error }}</div>
      <footer class="composer"><div class="composer-box"><textarea v-model="input" rows="1" placeholder="输入岗位市场或求职分析问题…" @keydown="handleComposerKeydown" @compositionstart="startComposition" @compositionend="endComposition"></textarea><div class="composer-meta"><span>{{ contextSummary }}</span><label class="rag-toggle"><input v-model="useJdRag" type="checkbox" /> <span>检索岗位 JD（RAG）</span></label><div class="composer-actions"><label class="shortcut-control">提交方式<select v-model="submitShortcut"><option value="enter">按 Enter 提交</option><option value="metaEnter">按 ⌘+Enter 提交</option></select></label><button class="send-button" :disabled="loading || !input.trim()" aria-label="发送" @click="submit()">↑</button></div></div></div><small>建议先明确岗位关键词、城市和比较口径；勾选“检索岗位 JD（RAG）”后，会使用当前岗位库中的 JD 证据回答。</small></footer>
    </main>

    <aside class="query-panel" :class="{ open: rightOpen }">
      <header class="panel-header"><div><p class="kicker">CAREER CONTEXT</p><h2>求职分析工具</h2></div><button @click="resetWorkspace">清空结果上下文</button></header>
      <p class="context-intro">这里管理会影响后续分析的材料：岗位结果用于跨问题对比，简历会自动随主输入框的问题提交。上传后直接提问即可，无需填写目标岗位表单。</p>
      <div class="context-summary">{{ contextSummary }}</div>

      <section class="context-block result-context-block"><div class="context-heading"><div><strong>已添加的岗位结果</strong><small>用于下一条问题的对比和综合分析</small></div><em>{{ selectedAnalysisTables.length }} 个</em></div><div class="analysis-tables-zone" :class="{ filled: selectedAnalysisTables.length, 'drop-ready': dragTarget === 'result' }" @dragover.prevent="setDragTarget('result')" @dragleave="clearDragTarget" @drop.prevent="dropAnalysisTable"><div v-if="!selectedAnalysisTables.length" class="empty-confirmation"><span>＋</span><strong>还没有添加岗位结果</strong><small>从下方结果列表点击“添加到分析上下文”，或拖入这里</small><button class="empty-action" @click="scrollToLibrary">去查看已保存结果</button></div><div v-for="table in selectedAnalysisTables" :key="table.taskId" class="analysis-table-chip"><span class="table-mark">▦</span><span><strong>{{ table.title }}</strong><small>{{ table.rowCount }} 行 · {{ table.query }}</small></span><button :aria-label="`移除${table.title}`" @click="removeAnalysisTable(table.taskId)">×</button></div></div></section>

      <section ref="resultLibrary" class="context-block result-library"><div class="library-heading"><div><strong>可添加的岗位结果</strong><small>保存查询结果后，会在这里出现</small></div><em>最多 {{ SAVED_TABLE_LIMIT }} 张</em></div><p v-if="savedActionError" class="panel-error">{{ savedActionError }}</p><p v-if="savedActionMessage" class="panel-success">{{ savedActionMessage }}</p><div v-if="!historicalResultTables.length" class="library-empty">先在左侧查询并点击“保存结果”，保存后的岗位列表会显示在这里。</div><div v-for="table in historicalResultTables" :key="table.taskId" class="table-library-row" draggable="true" @dragstart="dragAnalysisTable($event, table)" @dragend="clearDragTarget"><div class="recent-card"><span class="table-mark">▦</span><span><strong>{{ table.title }}</strong><small>{{ table.rowCount }} 行 · {{ table.query }}</small></span></div><div class="library-row-actions"><button class="context-add-button" :class="{ added: analysisTableIds.includes(table.taskId) }" :disabled="analysisTableIds.includes(table.taskId)" @click="addAnalysisTable(table.taskId)">{{ analysisTableIds.includes(table.taskId) ? "已加入上下文" : "加入上下文" }}</button><button class="delete-result-button" @click.stop="deleteSavedResult(table)">删除</button></div></div></section>
      <section class="resume-context-block" :class="{ expanded: resumePanelOpen }">
        <button v-if="!resumePanelOpen" class="resume-upload-button resume-upload-entry" type="button" @click="openResumePanel"><span>＋</span>上传我的简历</button>
        <template v-else>
          <div class="context-heading"><div><strong>我的简历</strong><small>上传后可在主输入框中直接提问分析</small></div><em v-if="resume">已就绪</em></div>
          <input ref="resumeInput" class="visually-hidden" type="file" accept=".txt,.text,.md,.markdown,.pdf,text/plain,application/pdf" @change="handleResumeSelected">
          <div v-if="resume" class="resume-ready"><span class="resume-icon">▤</span><div><strong>{{ resume.filename }}</strong><small>{{ resume.page_count }} 页 · {{ resume.skill_count }} 项技能 · {{ resume.project_count }} 个项目</small></div><button aria-label="删除当前简历" @click="removeResume">删除</button></div>
          <button v-else class="resume-upload-button" type="button" :disabled="resumeUploading" @click="openResumePicker"><span>＋</span>{{ resumeUploading ? "正在解析简历…" : "上传我的简历" }}</button>
          <ul class="resume-requirements"><li>支持 TXT / TEXT / MD / MARKDOWN / 可复制文本的 PDF</li><li>不超过 5 MB、最多 10 页；PDF 不能是扫描图片</li><li>文本建议使用 UTF-8，提取文本至少 10 个字符</li><li>暂不支持 DOC/DOCX、图片 OCR；仅保存结构化脱敏证据</li></ul>
          <p v-if="resumeError" class="panel-error">{{ resumeError }}</p>
          <button class="resume-collapse-button" type="button" @click="resumePanelOpen = false">收起简历提示</button>
        </template>
      </section>
    </aside>
    <CareerReportDialog :open="careerDialogOpen" :report="careerReport" @close="careerDialogOpen = false" @download="downloadCareerReport" />
  </div>
</template>
