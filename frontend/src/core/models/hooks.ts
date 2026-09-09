import { useQuery } from '@tanstack/react-query'

import { listModels } from './api'

export const modelListKey = ['models'] as const

export function useModels() {
  return useQuery({
    queryKey: modelListKey,
    queryFn: listModels,
    staleTime: Infinity,
  })
}