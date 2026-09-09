import { api } from '@/core/api/client'
import { type UploadOut } from './types'

export function uploadFile(threadId: string, file: File): Promise<UploadOut> {
  const form = new FormData()
  form.append('file', file, file.name)
  return api.post<UploadOut>(`/threads/${threadId}/uploads`, form)
}

export function listUploads(threadId: string): Promise<UploadOut[]> {
  return api.get<UploadOut[]>(`/threads/${threadId}/uploads`)
}