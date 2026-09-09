import { api, API_BASE } from '@/core/api/client'
import { type RunCreateBody, type RunOut } from '@/core/api/types'

export function createRun(threadId: string, body: RunCreateBody): Promise<RunOut> {
  return api.post<RunOut>(`/threads/${threadId}/runs`, body)
}

export function listThreadRuns(threadId: string, limit = 50): Promise<RunOut[]> {
  return api.get<RunOut[]>(`/threads/${threadId}/runs?limit=${limit}`)
}

export function getRun(runId: string): Promise<RunOut> {
  return api.get<RunOut>(`/runs/${runId}`)
}

export function cancelRun(runId: string): Promise<RunOut> {
  return api.post<RunOut>(`/runs/${runId}/cancel`)
}

export function runStreamUrl(runId: string): string {
  return `${API_BASE}/runs/${runId}/stream`
}