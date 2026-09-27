import { api } from '@/core/api/client'

import { type AgentMdOut, type AgentMdSavePayload } from './types'

/** 查询当前用户自定义指令（未设置时 content 为空串）。 */
export function getAgentMd(): Promise<AgentMdOut> {
  return api.get<AgentMdOut>('/agent-md')
}

/** 保存当前用户自定义指令（空串即清空）。 */
export function saveAgentMd(payload: AgentMdSavePayload): Promise<AgentMdOut> {
  return api.put<AgentMdOut>('/agent-md', payload)
}
