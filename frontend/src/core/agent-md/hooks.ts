import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { getAgentMd, saveAgentMd } from './api'
import { type AgentMdSavePayload } from './types'

export const agentMdKey = ['agent-md'] as const

export function useAgentMd(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: agentMdKey,
    queryFn: getAgentMd,
    // 未登录时不发请求（避免无意义的 401）
    enabled: options?.enabled ?? true,
  })
}

export function useSaveAgentMd() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: AgentMdSavePayload) => saveAgentMd(payload),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: agentMdKey })
    },
  })
}
