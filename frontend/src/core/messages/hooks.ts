import { useQuery, useQueryClient } from '@tanstack/react-query'

import { getThreadMessages } from './api'
import { type MessageOut } from './types'

export function messageListKey(threadId: string) {
  return ['messages', threadId] as const
}

export function useMessages(threadId: string) {
  return useQuery({
    queryKey: messageListKey(threadId),
    queryFn: () => getThreadMessages(threadId),
    enabled: Boolean(threadId),
    staleTime: 0,
  })
}

export function useMessagesUpdater() {
  const qc = useQueryClient()

  return {
    invalidate: (threadId: string) => void qc.invalidateQueries({ queryKey: messageListKey(threadId) }),
    setMessages: (threadId: string, messages: MessageOut[]) => {
      qc.setQueryData(messageListKey(threadId), messages)
    },
  }
}