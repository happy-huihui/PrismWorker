import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { cancelRun, fetchThreadChains, getRun, listThreadRuns } from './api'
import { type RunOut } from './types'

export function runListKey(threadId: string) {
  return ['runs', threadId] as const
}

export function chainListKey(threadId: string) {
  return ['chains', threadId] as const
}

export function runKey(runId: string) {
  return ['run', runId] as const
}

export function useThreadRuns(threadId: string) {
  return useQuery({
    queryKey: runListKey(threadId),
    queryFn: () => listThreadRuns(threadId),
    enabled: Boolean(threadId),
  })
}

/** 历史思考链：拉取本线程已落库 run 的事件流（供重开会话时回放） */
export function useThreadChains(threadId: string) {
  return useQuery({
    queryKey: chainListKey(threadId),
    queryFn: () => fetchThreadChains(threadId),
    enabled: Boolean(threadId),
  })
}

export function useRun(runId: string, enabled = true) {
  return useQuery({
    queryKey: runKey(runId),
    queryFn: () => getRun(runId),
    enabled: enabled && Boolean(runId),
    refetchIntervalInBackground: false,
  })
}

export function useCancelRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) => cancelRun(runId),
    onSuccess: (record: RunOut) => {
      qc.setQueryData<RunOut>(runKey(record.run_id), record)
      qc.invalidateQueries({ queryKey: ['runs', record.thread_id] })
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ['threads'] })
    },
  })
}