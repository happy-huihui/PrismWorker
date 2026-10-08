import { useQuery } from '@tanstack/react-query'

import { getObservabilityAnalytics, getObservabilityEvals, getObservabilityRun, getObservabilityStats, listObservabilityRuns } from './api'
import type { ObsRunListParams } from './types'

/**
 * 观测中台 Hooks（core/observability/hooks）
 *
 * 职责：用 React Query 封装观测接口的取数与缓存 key，与 core/runs 同款写法。
 */

export function obsRunsKey(params: ObsRunListParams) {
  return ['observability', 'runs', params] as const
}

export function obsRunKey(runId: string) {
  return ['observability', 'run', runId] as const
}

export function obsStatsKey(range: string) {
  return ['observability', 'stats', range] as const
}

export function obsAnalyticsKey(range: string) {
  return ['observability', 'analytics', range] as const
}

export function obsEvalsKey(experiment?: string) {
  return ['observability', 'evals', experiment ?? 'all'] as const
}

/** 观测运行列表（含 token/成本，按筛选/分页） */
export function useObservabilityRuns(params: ObsRunListParams = {}) {
  return useQuery({
    queryKey: obsRunsKey(params),
    queryFn: () => listObservabilityRuns(params),
  })
}

/** 单个运行详情（run + spans + logs + 思考链） */
export function useObservabilityRun(runId: string, enabled = true) {
  return useQuery({
    queryKey: obsRunKey(runId),
    queryFn: () => getObservabilityRun(runId),
    enabled: enabled && Boolean(runId),
  })
}

/** 总览：指标 + 趋势 + 模型占比 */
export function useObservabilityStats(range: string) {
  return useQuery({
    queryKey: obsStatsKey(range),
    queryFn: () => getObservabilityStats(range),
  })
}

/** 分析：模型 + 工具 + 成本 + 最贵 Top N */
export function useObservabilityAnalytics(range: string) {
  return useQuery({
    queryKey: obsAnalyticsKey(range),
    queryFn: () => getObservabilityAnalytics(range),
  })
}

/** 评测：实验汇总 + 明细（分数双存的本地侧） */
export function useObservabilityEvals(experiment?: string) {
  return useQuery({
    queryKey: obsEvalsKey(experiment),
    queryFn: () => getObservabilityEvals(experiment),
  })
}
