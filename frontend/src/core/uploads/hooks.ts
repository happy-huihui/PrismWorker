import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { listUploads, uploadFile } from './api'
import { type UploadOut } from './types'

export function uploadListKey(threadId: string) {
  return ['uploads', threadId] as const
}

export function useUploads(threadId: string) {
  return useQuery({
    queryKey: uploadListKey(threadId),
    queryFn: () => listUploads(threadId),
    enabled: Boolean(threadId),
  })
}

export function useUploadFiles(threadId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => uploadFile(threadId, file),
    onSuccess: (uploaded: UploadOut) => {
      qc.setQueryData<UploadOut[]>(uploadListKey(threadId), (old) => [
        uploaded,
        ...(old ?? []),
      ])
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: uploadListKey(threadId) })
    },
  })
}