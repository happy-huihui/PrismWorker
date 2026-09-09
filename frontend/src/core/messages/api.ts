import { api } from '@/core/api/client'
import { type MessageOut } from './types'

export function getThreadMessages(threadId: string): Promise<MessageOut[]> {
  return api.get<MessageOut[]>(`/threads/${threadId}/messages`)
}