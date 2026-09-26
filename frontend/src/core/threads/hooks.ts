import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { createThread, deleteThread, listThreads, renameThread } from './api'
import { type ThreadCreatePayload, type ThreadOut, type ThreadRenamePayload } from './types'

export const threadListKey = ['threads'] as const

export function useThreads(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: threadListKey,
    queryFn: listThreads,
    staleTime: 1000,
    // 未登录时不发请求（避免无意义的 401；侧栏改由 UI 展示登录引导）
    enabled: options?.enabled ?? true,
  })
}

export function useCreateThread() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: ThreadCreatePayload) => createThread(payload),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: threadListKey })
    },
  })
}

export function useRenameThread() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ threadId, payload }: { threadId: string; payload: ThreadRenamePayload }) =>
      renameThread(threadId, payload),
    onSuccess: (updated) => {
      qc.setQueriesData<ThreadOut[]>({ queryKey: threadListKey }, (old) =>
        old?.map((t) => (t.thread_id === updated.thread_id ? { ...t, ...updated } : t)),
      )
    },
    onSettled: (_data, _err, vars) => {
      void qc.invalidateQueries({ queryKey: threadListKey })
      void qc.invalidateQueries({ queryKey: ['thread', vars.threadId] })
    },
  })
}

export function useDeleteThread() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (threadId: string) => deleteThread(threadId),
    onSuccess: (_, threadId) => {
      qc.setQueriesData<ThreadOut[]>({ queryKey: threadListKey }, (old) =>
        old?.filter((t) => t.thread_id !== threadId),
      )
      qc.removeQueries({ queryKey: ['messages', threadId] })
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: threadListKey })
    },
  })
}