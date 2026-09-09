import { api } from '@/core/api/client'
import {
  type ThreadCreatePayload,
  type ThreadOut,
  type ThreadRenamePayload,
} from './types'

export function createThread(payload: ThreadCreatePayload): Promise<ThreadOut> {
  return api.post<ThreadOut>('/threads', payload)
}

export function listThreads(): Promise<ThreadOut[]> {
  return api.get<ThreadOut[]>('/threads')
}

export function renameThread(threadId: string, payload: ThreadRenamePayload): Promise<ThreadOut> {
  return api.patch<ThreadOut>(`/threads/${threadId}`, payload)
}

export function deleteThread(threadId: string): Promise<void> {
  return api.delete<void>(`/threads/${threadId}`)
}