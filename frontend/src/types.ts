export type CellValue = string | number | null

export interface ClarificationOption {
  id: string
  label: string
  description: string
  recommended: boolean
}

export interface SchemaHit {
  doc_id: string
  table_id: string
  table_label: string
  field_name: string
  field_label: string
  score: number
  source?: "retrieval" | "user_confirmed" | "relation_key"
  bm25_rank?: number | null
  dense_rank?: number | null
  rerank_score?: number
}

export interface SchemaGraph {
  database: string
  graph_version: string
  tables: { id: string; name?: string; label: string; description: string; domain: string; database: string }[]
  fields: {
    id: string
    table_id: string
    table_name?: string
    sql_name?: string
    name: string
    label: string
    type: string
    description: string
    role: string
    source: string
    score: number
  }[]
  joins: {
    left_table: string
    left_field: string
    right_table: string
    right_field: string
    description: string
    relation_type: string
  }[]
}

export interface DatabaseHandoff {
  database: { name: string; dialect: string; route: string }
  required_context: {
    schema_graph: SchemaGraph
    resolved_parameters: Record<string, unknown>
    tool_facts: { tool: string; arguments: Record<string, unknown>; result: Record<string, unknown> }[]
  }
  instruction: {
    task: string
    filters: string[]
    joins: string[]
    group_by: string[]
    metrics: string[]
    order_by: string[]
  }
  output_contract: {
    row_grain: string
    columns: { name: string; alias: string; type: string }[]
    max_rows: number
    empty_result_policy: string
  }
}

export interface CoderToolCall {
  call_index: number
  database: string
  arguments: DatabaseHandoff | {
    mode: "single_database_agent"
    transport: "mcp_in_process"
    tool_name?: string
    schema_graph_version?: string
    sql_source?: string
  }
  sql: string
  success: boolean
  row_count: number
  error?: string | null
}

export interface VisualizationSpec {
  type: "bar" | "pie" | "line" | "histogram"
  title: string
  source_task_id: string
  category_field: string
  value_field: string
  max_items: number
  bins?: number
}

export interface AnalysisReport {
  title: string
  markdown: string
  visualizations: VisualizationSpec[]
}

export interface ReportToolCall {
  call_index: number
  tool: "build_bar_chart" | "build_pie_chart" | "build_line_chart" | "build_histogram_chart"
  arguments: Record<string, unknown>
  result: VisualizationSpec
}

export interface SqlAttempt {
  attempt: number
  sql: string
  validation: "passed" | "rejected"
  execution: "succeeded" | "failed"
  error_kind?: string | null
  retryable: boolean
}

export interface AnalysisSource {
  task_id: string
  title: string
  query: string
  row_count: number
  columns: string[]
  column_labels?: Record<string, string>
}

export interface TargetJobRequest {
  keyword: string
  city?: string
  company?: string
  experience?: string
  degree?: string
}

export interface EvidenceRef {
  source: "resume" | "jd" | "system"
  quote: string
  location?: string | null
}

export interface JobProfileSkill {
  skill: string
  category: string
  occurrence_count: number
  denominator: number
  occurrence_rate: number
  requirement: "must_have" | "preferred" | "unknown"
  jd_evidence: EvidenceRef[]
  sample_size_note?: string | null
  extractor_version: string
}

export interface JobProfile {
  target: TargetJobRequest
  dataset_version: string
  sample_size: number
  valid_jd_count: number
  skills: JobProfileSkill[]
  salary_sample_count: number
  salary_p25_k?: number | null
  salary_p50_k?: number | null
  salary_p75_k?: number | null
  experience_distribution: Record<string, number>
  degree_distribution: Record<string, number>
  small_sample_warning?: string | null
}

export interface SkillCoverageItem {
  skill: string
  category: string
  status: "covered" | "partial" | "missing"
  requirement: "must_have" | "preferred" | "unknown"
  jd_evidence: EvidenceRef[]
  resume_evidence: EvidenceRef[]
  suggestion?: string | null
  confidence: number
  extractor_version: string
  jd_occurrence_rate?: number | null
}

export interface CareerReport {
  report_id: string
  title: string
  summary: string
  resume_id: string
  target: TargetJobRequest
  job_profile: JobProfile
  facts: string[]
  recommendations: string[]
  skill_coverage: SkillCoverageItem[]
  rewrite_directions: string[]
  interview_checklist: string[]
  generated_at: string
}

export interface ResumeUploadResponse {
  resume_id: string
  filename: string
  status: "ready"
  page_count: number
  skill_count: number
  project_count: number
  redaction_count: number
  message: string
}

export interface QueryResult {
  task_id: string
  status: "waiting_clarification" | "completed" | "failed"
  route: "database_query" | "data_qa" | "career_advice" | "direct_response" | "market_data_refresh"
  message: string
  interpretation: null | {
    metric: string
    dimension: string
    time_range: string
    table: string
    assumptions: string[]
  }
  clarification: null | {
    parameter?: string
    question: string
    reason: string
    options: ClarificationOption[]
  }
  steps: string[]
  sql: string | null
  columns: string[]
  column_labels: Record<string, string>
  rows: Record<string, CellValue>[]
  analysis: string | null
  saved: boolean
  result_title?: string | null
  standalone_query?: string | null
  schema_graph?: SchemaGraph | null
  retrieval?: null | {
    threshold: number
    bm25_count: number
    dense_count: number
    rrf_count: number
    candidate_count: number
    selected_count: number
    embedding_source: string
    rerank_source: string
    hits: SchemaHit[]
    schema_graph?: SchemaGraph
  }
  tool_calls?: CoderToolCall[]
  analysis_sources?: AnalysisSource[]
  workflow_mode?: "qa" | "single_database_fast_path" | "multi_database_handoff" | "langgraph_hitl" | string | null
  crawl_run_id?: string | null
  report?: AnalysisReport | null
  career_report?: CareerReport | null
  report_tool_calls?: ReportToolCall[]
  attempts?: SqlAttempt[]
  dataset_version?: string | null
  prompt_token_count?: number | null
}

export interface ReportDataSource {
  taskId: string
  title: string
  columns: string[]
  columnLabels?: Record<string, string>
  rows: CellValueMap[]
}

export type CellValueMap = Record<string, CellValue>

export interface WorkspaceConfig {
  analysis_table_ids?: string[]
  analysis_tables?: AnalysisTablePayload[]
}

export interface AnalysisTablePayload {
  task_id: string
  title: string
  query: string
  columns: string[]
  column_labels?: Record<string, string>
  rows: CellValueMap[]
}

export interface SavedMemory {
  id: string
  kind: "result_table"
  created_at: string
  task_id?: string
  query?: string
  summary?: string
  title?: string
  columns?: string[]
  column_labels?: Record<string, string>
  rows?: CellValueMap[]
  user_id?: string
}

export interface AuthUser {
  user_id: string
  username: string
  display_name: string
  role: "admin" | "current_sales" | string
}

export interface LoginResponse {
  access_token: string
  token_type: "bearer"
  user: AuthUser
}

export interface TaskEvent {
  event_id: string
  task_id: string
  sequence: number
  event_type: string
  timestamp: string
  payload: Record<string, string | number | boolean | null>
}

export interface TaskSnapshot {
  task_id: string
  status: "created" | "running" | "waiting_clarification" | "completed" | "failed" | "cancelled"
  sequence: number
  session_id: string
  result?: { summary: string; steps: string[]; data: Record<string, unknown> } | null
  error?: { code: string; message: string; detail?: string | null } | null
}

export type CrawlStatus = "queued" | "running" | "blocked" | "etl_running" | "succeeded" | "failed" | "cancelled"
export type CrawlPhase = "preflight" | "login" | "list" | "detail" | "etl" | "publish" | "terminal"

export interface CrawlFilters {
  scale?: string | null
  stage?: string | null
  salary?: string | null
  experience?: string | null
  degree?: string | null
  industry?: string | null
}

export interface CrawlRequest {
  keyword: string
  city: string
  pages: number
  detail: boolean
  max_details?: number | null
  filters: CrawlFilters
  source: "chat" | "frontend"
  confirmation: boolean
  auto_close_chrome: boolean
  run_smoke_test: boolean
}

export interface CrawlWarning {
  code: string
  message: string
}

export interface CrawlFailure {
  code: string
  message: string
  diagnostic?: string | null
}

export interface CrawlRunSnapshot {
  run_id: string
  owner_id: string
  status: CrawlStatus
  phase: CrawlPhase
  request: CrawlRequest
  requested_pages: number
  completed_pages: number
  current_page: number | null
  page_job_count: number
  new_job_count: number
  job_count: number
  detail_total: number | null
  current_detail: number | null
  detail_count: number
  current_item: string | null
  waiting_seconds_remaining: number | null
  waiting_seconds_total: number | null
  waiting_reason: string | null
  elapsed_seconds: number
  current_message: string
  warnings: CrawlWarning[]
  dataset_version?: string | null
  scraper_version: string
  etl_version?: string | null
  failure?: CrawlFailure | null
  started_at?: string | null
  finished_at?: string | null
  updated_at: string
  sequence: number
}

export interface CrawlEvent {
  event_id: string
  run_id: string
  sequence: number
  event_type: string
  timestamp: string
  payload: Record<string, unknown>
}

export interface CrawlPreflightResult {
  status: "ready" | "cdp_unavailable" | "login_required" | "risk_control" | "failed"
  message: string
  check_passed: boolean
  smoke_test_passed?: boolean | null
}

export interface CrawlOptions {
  cities: string[]
  max_pages: number
  max_details: number
  filters: Record<string, { code: string; label: string }[]>
}

export interface CrawlBrowserResult {
  status: "started" | "stopped" | "failed"
  message: string
}

export interface MarketDataBreakdown {
  label: string
  count: number
  ratio: number
}

export interface MarketDataSummary {
  available: boolean
  dataset_version?: string | null
  job_count: number
  detail_count: number
  detail_coverage: number
  first_scraped_at?: string | null
  latest_scraped_at?: string | null
  top_cities: MarketDataBreakdown[]
  top_keywords: MarketDataBreakdown[]
  top_industries: MarketDataBreakdown[]
  raw_available: boolean
  raw_job_count: number
  raw_detail_count: number
  raw_latest_scraped_at?: string | null
  raw_pending_publish: boolean
}

export interface MarketDataRun {
  run_id: string
  keyword: string
  city: string
  status: string
  source_status: string
  requested_pages?: number | null
  completed_pages?: number | null
  page_count?: number | null
  page_count_estimated: boolean
  job_count: number
  detail_count: number
  started_at: string
  finished_at: string
  dataset_version: string
  etl_version: string
  archive_available: boolean
  deletable: boolean
  delete_block_reason?: string | null
}

export interface MarketDataDeleteResult {
  deleted: boolean
  run_id: string
  dataset_version?: string | null
  message: string
}
