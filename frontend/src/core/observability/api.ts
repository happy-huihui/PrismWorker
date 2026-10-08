import { api } from '@/core/api/client'

import type {
  AnalyticsResponse,
  EvalsResponse,
  ObsRunListParams,
  ObsRunListResponse,
  RunDetailOut,
  StatsResponse,
} from './types'

/**
 * 观测中台 API（core/observability/api）
 *
 * 职责：封装后端 /observability 路由的调用，只做请求/响应类型收窄。
 */

export function listObservabilityRuns(params: ObsRunListParams = {}): Promise<ObsRunListResponse> {
  // 1.只拼非空参数，避免 query 里塞 undefined
  const q = new URLSearchParams()
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  if (params.status) q.set('status', params.status)
  if (params.model_name) q.set('model_name', params.model_name)
  if (params.q) q.set('q', params.q)
  if (params.range) q.set('range', params.range)
  const qs = q.toString()
  return api.get<ObsRunListResponse>(`/observability/runs${qs ? `?${qs}` : ''}`)
}

export function getObservabilityRun(runId: string): Promise<RunDetailOut> {
  return api.get<RunDetailOut>(`/observability/runs/${runId}`)
}

export function getObservabilityStats(range: string): Promise<StatsResponse> {
  return api.get<StatsResponse>(`/observability/stats?range=${range}`)
}

export function getObservabilityAnalytics(range: string): Promise<AnalyticsResponse> {
  return api.get<AnalyticsResponse>(`/observability/analytics?range=${range}`)
}

export function getObservabilityEvals(experiment?: string): Promise<EvalsResponse> {
  // 1.可选按实验过滤（None=全部）
  const q = experiment ? `?experiment=${encodeURIComponent(experiment)}` : ''
  return api.get<EvalsResponse>(`/observability/evals${q}`)
}
