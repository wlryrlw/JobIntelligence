<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue"
import { api } from "../api"
import type { CrawlEvent, CrawlOptions, CrawlPreflightResult, CrawlRequest, CrawlRunSnapshot, MarketDataBreakdown, MarketDataRun, MarketDataSummary } from "../types"

const props = defineProps<{ initialRunId?: string | null }>()

const options = ref<CrawlOptions | null>(null)
const run = ref<CrawlRunSnapshot | null>(null)
const preflight = ref<CrawlPreflightResult | null>(null)
const browserMessage = ref("")
const error = ref("")
const loading = ref(false)
const checking = ref(false)
const browserLoading = ref(false)
const streamController = ref<AbortController | null>(null)
const open = ref(false)
const dataSummary = ref<MarketDataSummary | null>(null)
const summaryLoading = ref(false)
const summaryError = ref("")
const runHistory = ref<MarketDataRun[]>([])
const historyOpen = ref(false)
const historyLoading = ref(false)
const historyError = ref("")
const deletingRunId = ref<string | null>(null)
const progressLog = ref<Array<{ sequence: number; timestamp: string; message: string }>>([])

const form = ref<CrawlRequest>({
  keyword: "AI Agent",
  city: "上海",
  pages: 3,
  detail: true,
  max_details: null,
  filters: {},
  source: "frontend",
  confirmation: false,
  auto_close_chrome: false,
  run_smoke_test: false,
})

const filterNames = [
  ["scale", "公司规模"],
  ["stage", "融资阶段"],
  ["salary", "薪资范围"],
  ["experience", "经验要求"],
  ["degree", "学历要求"],
  ["industry", "行业"],
] as const

const active = computed(() => Boolean(run.value && ["queued", "running", "blocked", "etl_running"].includes(run.value.status)))
const progress = computed(() => {
  if (!run.value) return 0
  if (run.value.status === "succeeded") return 100
  if (run.value.status === "etl_running") return 94
  if (run.value.phase === "detail") return Math.min(90, 25 + Math.min(65, run.value.detail_count))
  return Math.min(85, Math.round((run.value.completed_pages / Math.max(1, run.value.requested_pages)) * 85))
})

function filterOptions(name: string) {
  return options.value?.filters[name] ?? []
}

function setFilter(name: string, value: string) {
  form.value.filters = { ...form.value.filters, [name]: value || null }
}

function filterValue(name: string) {
  return form.value.filters[name as keyof typeof form.value.filters] ?? ""
}

function statusLabel(status: string) {
  return ({ queued: "排队中", running: "采集中", blocked: "等待人工处理", etl_running: "正在发布", succeeded: "已完成", failed: "失败", cancelled: "已取消" } as Record<string, string>)[status] ?? status
}

function phaseLabel(phase: string) {
  return ({ preflight: "检查环境", login: "等待登录", list: "采集列表", detail: "采集详情", etl: "数据校验", publish: "发布数据", terminal: "已结束" } as Record<string, string>)[phase] ?? phase
}

function elapsed(seconds: number) {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} 秒`
  return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`
}

function pageProgress(snapshot: CrawlRunSnapshot) {
  const current = snapshot.current_page ?? snapshot.completed_pages
  return `第 ${current}/${snapshot.requested_pages} 页`
}

function waitingLabel(reason: string | null) {
  return ({
    "翻页等待": "等待翻到下一页",
    "详情间隔等待": "等待下一个岗位详情",
    "页面加载等待": "等待页面加载",
  } as Record<string, string>)[reason || ""] ?? "等待 scraper 继续处理"
}

function eventNumber(payload: Record<string, unknown>, key: string, fallback: number | null) {
  if (!(key in payload)) return fallback
  return payload[key] == null ? null : Number(payload[key])
}

function eventString(payload: Record<string, unknown>, key: string, fallback: string | null) {
  if (!(key in payload)) return fallback
  return payload[key] == null ? null : String(payload[key])
}

function formatCount(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value)
}

function formatDate(value?: string | null) {
  if (!value) return "未知"
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return "未知"
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(parsed)
}

function visibleBreakdowns(items: MarketDataBreakdown[]) {
  return items.slice(0, 3)
}

async function loadSummary() {
  summaryLoading.value = true
  summaryError.value = ""
  try {
    dataSummary.value = await api.marketDataSummary()
  } catch (cause) {
    summaryError.value = cause instanceof Error ? cause.message : "无法读取岗位数据概览"
  } finally {
    summaryLoading.value = false
  }
}

async function loadRunHistory() {
  historyLoading.value = true
  historyError.value = ""
  try {
    runHistory.value = await api.marketDataRuns()
  } catch (cause) {
    historyError.value = cause instanceof Error ? cause.message : "无法读取采集历史"
  } finally {
    historyLoading.value = false
  }
}

async function openHistory() {
  historyOpen.value = true
  await loadRunHistory()
}

function closeHistory() {
  if (!deletingRunId.value) historyOpen.value = false
}

function runFeatures(item: MarketDataRun) {
  const pages = pageLabel(item)
  const detail = item.detail_count > 0 ? "包含 JD 详情" : "仅岗位列表"
  return `${item.keyword || "未标注关键词"} · ${item.city || "未标注城市"} · ${pages} · ${detail}`
}

function pageLabel(item: MarketDataRun) {
  if (item.completed_pages != null && item.requested_pages != null) {
    return `${item.completed_pages}/${item.requested_pages} 页`
  }
  if (item.page_count != null) {
    return `${item.page_count} 页${item.page_count_estimated ? "（估算）" : ""}`
  }
  return "页数未记录"
}

async function deleteRun(item: MarketDataRun) {
  if (!item.deletable || deletingRunId.value) return
  const confirmed = window.confirm(
    `确定删除 ${formatDate(item.finished_at)} 的采集数据吗？这会重建当前岗位信息，删除后不可从 JobIntel 恢复。`,
  )
  if (!confirmed) return
  deletingRunId.value = item.run_id
  historyError.value = ""
  try {
    await api.deleteMarketDataRun(item.run_id)
    await Promise.all([loadSummary(), loadRunHistory()])
  } catch (cause) {
    historyError.value = cause instanceof Error ? cause.message : "删除采集批次失败"
  } finally {
    deletingRunId.value = null
  }
}

function applyEvent(event: CrawlEvent) {
  if (!run.value) return
  const payload = event.payload
  const message = eventString(payload, "message", run.value.current_message) || run.value.current_message
  run.value = {
    ...run.value,
    sequence: event.sequence,
    status: (payload.status as CrawlRunSnapshot["status"]) || run.value.status,
    phase: (payload.phase as CrawlRunSnapshot["phase"]) || run.value.phase,
    current_message: message,
    completed_pages: Number(payload.completed_pages ?? run.value.completed_pages),
    current_page: eventNumber(payload, "current_page", run.value.current_page),
    page_job_count: Number(payload.page_job_count ?? run.value.page_job_count),
    new_job_count: Number(payload.new_job_count ?? run.value.new_job_count),
    job_count: Number(payload.job_count ?? run.value.job_count),
    detail_total: eventNumber(payload, "detail_total", run.value.detail_total),
    current_detail: eventNumber(payload, "current_detail", run.value.current_detail),
    detail_count: Number(payload.detail_count ?? run.value.detail_count),
    current_item: eventString(payload, "current_item", run.value.current_item),
    waiting_seconds_remaining: eventNumber(payload, "waiting_seconds_remaining", run.value.waiting_seconds_remaining),
    waiting_seconds_total: eventNumber(payload, "waiting_seconds_total", run.value.waiting_seconds_total),
    waiting_reason: eventString(payload, "waiting_reason", run.value.waiting_reason),
    elapsed_seconds: Number(payload.elapsed_seconds ?? run.value.elapsed_seconds),
    dataset_version: (payload.dataset_version as string | null) ?? run.value.dataset_version,
    failure: (payload.failure as CrawlRunSnapshot["failure"]) ?? run.value.failure,
  }
  if (message && !progressLog.value.some((item) => item.sequence === event.sequence)) {
    progressLog.value = [...progressLog.value, { sequence: event.sequence, timestamp: event.timestamp, message }].slice(-8)
  }
  if (event.event_type === "crawl.dataset_published" || payload.status === "succeeded") {
    void loadSummary()
    if (historyOpen.value) void loadRunHistory()
  }
}

async function loadOptions() {
  try {
    options.value = await api.crawlOptions()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "无法加载采集选项"
  }
}

async function checkEnvironment() {
  checking.value = true
  error.value = ""
  try {
    preflight.value = await api.preflightCrawl(form.value.run_smoke_test)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "环境检查失败"
  } finally {
    checking.value = false
  }
}

async function startBrowser() {
  if (!window.confirm("将启动 BOSS 专用 Chrome。请只在该窗口中登录，不会复制主 Chrome Cookie。继续吗？")) return
  browserLoading.value = true
  error.value = ""
  try {
    const result = await api.startCrawlBrowser()
    browserMessage.value = result.message
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "专用 Chrome 启动失败"
  } finally {
    browserLoading.value = false
  }
}

async function stopBrowser() {
  browserLoading.value = true
  try {
    const result = await api.stopCrawlBrowser()
    browserMessage.value = result.message
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "专用 Chrome 关闭失败"
  } finally {
    browserLoading.value = false
  }
}

async function subscribe(runId: string) {
  streamController.value?.abort()
  const controller = new AbortController()
  streamController.value = controller
  try {
    await api.streamCrawlEvents(runId, applyEvent, controller.signal)
  } catch (cause) {
    if (!controller.signal.aborted) {
      error.value = cause instanceof Error ? `${cause.message}，可点击“重新检查”或刷新后按运行 ID 查看` : "采集进度连接中断"
      try {
        run.value = await api.getCrawlRun(runId)
      } catch (refreshCause) {
        const suffix = refreshCause instanceof Error ? `；刷新状态也失败：${refreshCause.message}` : "；刷新状态也失败"
        error.value += suffix
      }
    }
  }
}

async function attachRun(runId: string) {
  try {
    if (run.value?.run_id !== runId) progressLog.value = []
    run.value = await api.getCrawlRun(runId)
    await subscribe(runId)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "无法恢复对话中的采集任务"
  }
}

async function start() {
  if (!form.value.keyword.trim() || !form.value.city.trim()) {
    error.value = "请填写关键词和城市"
    return
  }
  loading.value = true
  error.value = ""
  try {
    progressLog.value = []
    const checked = await api.preflightCrawl(form.value.run_smoke_test)
    preflight.value = checked
    if (checked.status !== "ready") {
      error.value = checked.message
      return
    }
    run.value = await api.createCrawlRun({ ...form.value, confirmation: true })
    await subscribe(run.value.run_id)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "创建采集任务失败"
  } finally {
    loading.value = false
  }
}

async function cancel() {
  if (!run.value) return
  try {
    run.value = await api.cancelCrawlRun(run.value.run_id)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "取消采集失败"
  }
}

async function resume() {
  if (!run.value) return
  try {
    run.value = await api.resumeCrawlRun(run.value.run_id)
    await subscribe(run.value.run_id)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "恢复采集失败"
  }
}

onMounted(() => {
  void loadOptions()
  void loadSummary()
  if (props.initialRunId) void attachRun(props.initialRunId)
})
watch(() => props.initialRunId, (runId) => {
  if (runId && runId !== run.value?.run_id) void attachRun(runId)
})
onUnmounted(() => streamController.value?.abort())
</script>

<template>
  <button class="crawl-launcher" type="button" @click="open = true">
    <span class="crawl-launcher-icon">↻</span>
    <span><strong>更新岗位数据</strong><small>{{ active ? `后台${statusLabel(run?.status || "running")}` : "采集 BOSS 岗位并发布" }}</small></span>
  </button>

  <section class="market-summary-card" aria-label="当前岗位信息">
    <div class="market-summary-heading">
      <div><strong>当前岗位信息</strong><small>对话查询使用此数据</small></div>
      <div class="market-summary-actions">
        <button class="market-history-button" type="button" @click="openHistory">详情</button>
        <button type="button" :disabled="summaryLoading" @click="loadSummary">{{ summaryLoading ? "读取中" : "刷新" }}</button>
      </div>
    </div>
    <p v-if="summaryError" class="market-summary-error">{{ summaryError }}</p>
    <p v-else-if="summaryLoading && !dataSummary" class="market-summary-empty">正在读取数据概览…</p>
    <p v-else-if="!dataSummary?.available && dataSummary?.raw_available" class="market-summary-empty">已发现 {{ formatCount(dataSummary.raw_job_count) }} 条原始抓取结果，但尚未发布为可查询快照。</p>
    <p v-else-if="!dataSummary?.available" class="market-summary-empty">尚未找到已发布岗位信息</p>
    <template v-else>
      <div class="market-summary-counts">
        <div><strong>{{ formatCount(dataSummary.job_count) }}</strong><small>可查询岗位</small></div>
        <div><strong>{{ formatCount(dataSummary.detail_count) }}</strong><small>有效 JD</small></div>
        <div><strong>{{ Math.round(dataSummary.detail_coverage * 100) }}%</strong><small>JD 覆盖</small></div>
      </div>
      <p class="market-summary-meta">{{ formatDate(dataSummary.first_scraped_at) }} – {{ formatDate(dataSummary.latest_scraped_at) }} · {{ dataSummary.dataset_version }}</p>
      <div v-for="[title, items] in [['主要城市', dataSummary.top_cities], ['关键词', dataSummary.top_keywords], ['行业', dataSummary.top_industries]]" :key="title" class="market-summary-breakdown">
        <span>{{ title }}</span>
        <div><em v-for="item in visibleBreakdowns(items)" :key="`${title}-${item.label}`">{{ item.label }} {{ formatCount(item.count) }}</em></div>
      </div>
    </template>
    <p v-if="dataSummary?.available && dataSummary.raw_available" class="market-summary-source">原始抓取结果 {{ formatCount(dataSummary.raw_job_count) }} 条<span v-if="dataSummary.raw_pending_publish"> · 有新结果待 ETL 发布</span><span v-else> · 与当前快照一致</span></p>
  </section>

  <Teleport to="body">
    <div v-if="historyOpen" class="market-history-backdrop" @click.self="closeHistory">
      <section class="market-history-modal" role="dialog" aria-modal="true" aria-labelledby="market-history-title">
        <header class="market-history-header">
          <div><p class="kicker">DATA HISTORY</p><h2 id="market-history-title">岗位数据采集历史</h2><small>已采集 {{ runHistory.length }} 次；删除后会重新发布剩余岗位数据</small></div>
          <button class="crawl-modal-close" type="button" aria-label="关闭岗位数据采集历史" @click="closeHistory">×</button>
        </header>
        <div class="market-history-body">
          <p v-if="historyError" class="panel-error">{{ historyError }}</p>
          <p v-if="historyLoading" class="market-history-empty">正在读取采集历史…</p>
          <p v-else-if="!runHistory.length" class="market-history-empty">暂无已发布采集记录</p>
          <div v-else class="market-run-list">
            <article v-for="item in runHistory" :key="item.run_id" class="market-run-card">
              <header class="market-run-heading">
                <div><strong>{{ formatDate(item.finished_at) }}</strong><small>{{ item.keyword || "未标注关键词" }} · {{ item.city || "未标注城市" }}</small></div>
                <span :class="item.deletable ? 'market-run-status-ready' : 'market-run-status-blocked'">{{ item.deletable ? "可删除" : "不可删除" }}</span>
              </header>
              <div class="market-run-stats"><span>岗位 {{ formatCount(item.job_count) }}</span><span>详情 {{ formatCount(item.detail_count) }}</span><span>页数 {{ pageLabel(item) }}</span></div>
              <p class="market-run-features">主要特征：{{ runFeatures(item) }}</p>
              <p class="market-run-version">数据集 {{ item.dataset_version }} · ETL {{ item.etl_version }}</p>
              <p v-if="!item.deletable" class="market-run-block">{{ item.delete_block_reason }}</p>
              <button class="market-run-delete" type="button" :disabled="!item.deletable || deletingRunId === item.run_id" @click="deleteRun(item)">{{ deletingRunId === item.run_id ? "删除中…" : "删除本次数据" }}</button>
            </article>
          </div>
        </div>
      </section>
    </div>
  </Teleport>

  <Teleport to="body">
    <div v-if="open" class="crawl-modal-backdrop" @click.self="open = false">
      <section class="crawl-modal" role="dialog" aria-modal="true" aria-labelledby="crawl-modal-title">
        <header class="crawl-modal-header">
          <div><p class="kicker">MARKET DATA</p><h2 id="crawl-modal-title">更新岗位数据</h2><small>专用 Chrome · 环境检查 · 抓取 · ETL 发布</small></div>
          <button class="crawl-modal-close" type="button" aria-label="关闭更新岗位数据窗口" @click="open = false">×</button>
        </header>
        <div class="crawl-panel">
    <div class="context-heading">
      <div><strong>更新岗位数据</strong><small>启动专用 Chrome → 检查环境 → 抓取 → 自动发布</small></div>
      <em v-if="preflight" :class="`crawl-dot ${preflight.status}`">{{ preflight.status === "ready" ? "可用" : "需处理" }}</em>
    </div>
    <p class="crawl-notice">采集会访问已登录的 BOSS 专用 Chrome。列表页和详情页之间有等待间隔，任务可能持续数分钟；进度提示会明确显示当前等待阶段。</p>

    <div class="crawl-browser-actions">
      <button type="button" :disabled="browserLoading || active" @click="startBrowser">{{ browserLoading ? "处理中…" : "启动专用 Chrome" }}</button>
      <button type="button" :disabled="checking || active" @click="checkEnvironment">{{ checking ? "检查中…" : "检查环境" }}</button>
      <button type="button" class="crawl-secondary" :disabled="browserLoading" @click="stopBrowser">关闭专用 Chrome</button>
    </div>
    <p v-if="browserMessage" class="crawl-hint">{{ browserMessage }}</p>
    <p v-if="preflight" class="crawl-preflight" :class="preflight.status">{{ preflight.message }}<span v-if="preflight.smoke_test_passed"> · smoke test 通过</span></p>

    <div class="crawl-form">
      <label><span>岗位关键词</span><input v-model="form.keyword" :disabled="active" placeholder="例如：AI Agent"></label>
      <label><span>城市</span><input v-model="form.city" :disabled="active" list="crawl-city-options" placeholder="例如：上海或 101020100"><datalist id="crawl-city-options"><option v-for="city in options?.cities" :key="city" :value="city" /></datalist></label>
      <div class="crawl-form-grid">
        <label><span>抓取页数</span><select v-model.number="form.pages" :disabled="active"><option v-for="page in options?.max_pages ?? 10" :key="page" :value="page">{{ page }} 页</option></select></label>
        <label><span>最大详情数</span><input v-model.number="form.max_details" :disabled="active" type="number" min="1" :max="options?.max_details ?? 200" placeholder="不限"></label>
      </div>
      <div class="crawl-filter-grid">
        <label v-for="[name, label] in filterNames" :key="name"><span>{{ label }}</span><select :value="filterValue(name)" :disabled="active" @change="setFilter(name, ($event.target as HTMLSelectElement).value)"><option value="">不限</option><option v-for="item in filterOptions(name)" :key="item.code" :value="item.code">{{ item.label }}</option></select></label>
      </div>
      <label class="crawl-check"><input v-model="form.detail" :disabled="active" type="checkbox"><span>抓取 JD 详情</span><small>关闭后只能分析列表字段</small></label>
      <label class="crawl-check"><input v-model="form.run_smoke_test" :disabled="active" type="checkbox"><span>开始前运行 smoke test</span><small>更稳妥，但会增加一次环境检查</small></label>
      <label class="crawl-check"><input v-model="form.auto_close_chrome" :disabled="active" type="checkbox"><span>成功后自动关闭专用 Chrome</span><small>失败、登录失效或风控时仍保留窗口</small></label>
      <button class="crawl-start" type="button" :disabled="loading || active || !options" @click="start">{{ loading ? "创建中…" : "检查并开始采集" }}</button>
    </div>

    <div v-if="run" class="crawl-run-card">
      <div class="crawl-run-heading"><strong>{{ statusLabel(run.status) }}</strong><span>{{ phaseLabel(run.phase) }} · {{ elapsed(run.elapsed_seconds) }}</span></div>
      <div class="crawl-progress-track"><i :style="{ width: `${progress}%` }"></i></div>
      <p class="crawl-current-message">{{ run.current_message }}</p>
      <div class="crawl-progress-overview">
        <div><strong>{{ pageProgress(run) }}</strong><small>列表采集进度 · 已完成 {{ run.completed_pages }}/{{ run.requested_pages }} 页</small></div>
        <div><strong>{{ formatCount(run.job_count) }}</strong><small>已采集岗位（累计）</small></div>
        <div v-if="run.phase === 'list'"><strong>{{ formatCount(run.page_job_count) }}</strong><small>当前页岗位 · 新增 {{ formatCount(run.new_job_count) }}</small></div>
        <div v-else><strong>{{ run.current_detail ?? 0 }}/{{ run.detail_total ?? "—" }}</strong><small>岗位详情进度 · 已完成 {{ formatCount(run.detail_count) }} 个</small></div>
      </div>
      <div v-if="run.waiting_seconds_remaining != null" class="crawl-waiting-card">
        <span class="crawl-waiting-icon">◷</span>
        <div><strong>正在等待 {{ run.waiting_seconds_remaining }} 秒</strong><small>{{ waitingLabel(run.waiting_reason) }}<span v-if="run.waiting_seconds_total != null"> · 本次约 {{ run.waiting_seconds_total }} 秒</span></small></div>
      </div>
      <p v-if="run.current_item" class="crawl-current-item"><span>当前处理</span>{{ run.current_item }}</p>
      <div v-if="progressLog.length" class="crawl-progress-log">
        <div class="crawl-progress-log-heading"><span>最近进度</span><small>实时更新</small></div>
        <p v-for="item in progressLog" :key="item.sequence"><time>{{ formatDate(item.timestamp) }}</time><span>{{ item.message }}</span></p>
      </div>
      <p v-if="run.failure" class="panel-error">{{ run.failure.message }}</p>
      <p v-if="run.status === 'succeeded'" class="panel-success">数据集 {{ run.dataset_version }} 已发布；后续 SQL 查询将使用最新版本。</p>
      <p v-for="warning in run.warnings" :key="warning.code" class="crawl-warning">{{ warning.message }}</p>
      <div class="crawl-run-actions"><button v-if="active" type="button" @click="cancel">取消采集</button><button v-if="run.status === 'blocked'" type="button" @click="resume">完成处理后重新检查</button><button v-if="run.status === 'succeeded' || run.status === 'failed'" type="button" class="crawl-secondary" @click="checkEnvironment">重新检查环境</button></div>
    </div>
    <p v-if="error" class="panel-error">{{ error }}</p>
        </div>
      </section>
    </div>
  </Teleport>
</template>
