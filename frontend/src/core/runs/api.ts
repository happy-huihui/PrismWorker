import { api, API_BASE } from '@/core/api/client'
import { type ChainOut, type RunCreateBody, type RunOut } from '@/core/api/types'

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

/** 取一个线程的历史思考链回放包（已落库 run，创建时间正序） */
export function fetchThreadChains(threadId: string): Promise<ChainOut[]> {
  return api.get<ChainOut[]>(`/threads/${threadId}/chains`)
}