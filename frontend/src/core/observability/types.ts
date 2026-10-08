import type { RunOut } from '@/core/api/types'

/**
 * 观测中台数据类型（core/observability/types）
 *
 * 职责：定义观测中台的前端契约——运行列表项（含 token/成本）、span（调用树，富属性）、
 *       log（结构化日志）、思考链事件、运行详情、总览/分析聚合。与后端
 *       app/api/routes/observability.py + schemas 一一对应。
 */

/** 观测运行项：RunOut + token/成本（后端已带，这里显式声明） */
export interface ObsRunOut extends RunOut {
  trace_id: string
  total_tokens: number
  cost: number
}

/** span 类型：llm / tool / retrieval / agent */
export type SpanType = 'llm' | 'tool' | 'retrieval' | 'agent' | string

/** span 富属性（存原文，观测中台详情页展示输入/输出/参数用） */
export interface SpanAttributes {
  /** tool span：工具调用 id 与参数 */
  tool_call_id?: string
  args?: Record<string, unknown>
  /** tool span：执行结果原文 */
  result?: string
  /** llm span：发给模型的完整消息列表 */
  messages?: Array<{ role: string; content: string }>
  /** llm span：输出正文原文 */
  output?: string
  /** llm span：本轮发起的工具调用 */
  tool_calls?: Array<{ name: string; args?: unknown }>
  /** llm span：provider 的 finish_reason */
  finish_reason?: string
  /** llm span：模型请求真实采样参数（temperature/max_tokens 等，后端白名单采集） */
  params?: Record<string, unknown>
  /** llm span：思考 token 数（provider 不回则 0） */
  reasoning_tokens?: number
  [k: string]: unknown
}

/** 一条 span（一次模型/工具/检索调用步骤），字段与 spans 表对齐 */
export interface SpanOut {
  span_id: string
  parent_span_id: string | null
  trace_id: string
  run_id: string
  thread_id: string
  user_id: string
  type: SpanType
  name: string
  started_at: number
  finished_at: number | null
  duration_ms: number | null
  model_name: string | null
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  cost: number | null
  error: string | null
  attributes: SpanAttributes
  created_at: number
}

/** 一条结构化日志（logs 表一行，fields 已反序列化） */
export interface LogOut {
  log_id: number
  trace_id: string
  run_id: string
  span_id: string
  level: string
  logger: string
  event: string
  fields: Record<string, unknown>
  timestamp: number
}

/** 思考链事件（与 SSE 帧同构，runs.events 反序列化） */
export interface ChainEvent {
  event: string
  data: Record<string, unknown>
}

/** 运行详情：run 汇总 + span 调用树 + 日志流 + 思考链回放 */
export interface RunDetailOut {
  run: ObsRunOut
  spans: SpanOut[]
  logs: LogOut[]
  chain_events: ChainEvent[]
}

/** 运行列表查询参数（对应 GET /observability/runs 的 query） */
export interface ObsRunListParams {
  limit?: number
  offset?: number
  status?: string
  model_name?: string
  q?: string
  range?: 'today' | '7d' | '30d' | 'all'
}

/** 运行列表响应：分页数据 + 总数 */
export interface ObsRunListResponse {
  items: ObsRunOut[]
  total: number
}

/** 总览核心指标（后端 overview_stats） */
export interface OverviewStats {
  total: number
  finished: number
  errors: number
  cancelled: number
  active: number
  tokens: number
  cost: number
  in_tokens: number
  out_tokens: number
  p50_duration: number
  p95_duration: number
  success_rate: number
}

/** 按天趋势点 */
export interface DailyPoint {
  day: string
  runs: number
  cost: number
}

/** 模型聚合行 */
export interface ModelAggregate {
  model: string
  calls: number
  in_tokens: number
  out_tokens: number
  cost: number
  avg_duration_ms: number
}

/** 工具聚合行 */
export interface ToolAggregate {
  tool: string
  calls: number
  success_rate: number
  avg_duration_ms: number
}

/** 最贵 run */
export interface TopCostRun {
  run_id: string
  user_id: string
  input_preview: string
  model_name: string
  cost: number
  created_at: number
}

/** GET /observability/stats 响应 */
export interface StatsResponse {
  overview: OverviewStats
  /** 上一同长窗口对比（today 比昨天 / 7d·30d 比上一窗口；all 为 null），供「较上期」涨跌 */
  overview_prev: OverviewStats | null
  daily: DailyPoint[]
  models: ModelAggregate[]
}

/** GET /observability/analytics 响应 */
export interface AnalyticsResponse {
  models: ModelAggregate[]
  tools: ToolAggregate[]
  daily: DailyPoint[]
  top_cost: TopCostRun[]
}

/** 评测聚合行（按 experiment+metric 求平均，供中台评测页版本对比） */
export interface EvalSummary {
  experiment: string
  metric: string
  n: number
  avg_score: number
  latest: number
  model_name: string
}

/** 评测明细行（每条示例 × 每个 metric 一分） */
export interface EvalDetail {
  eval_id: number
  experiment: string
  question: string
  metric: string
  score: number
  output: string
  model_name: string
  created_at: number
}

/** GET /observability/evals 响应 */
export interface EvalsResponse {
  summary: EvalSummary[]
  details: EvalDetail[]
}
