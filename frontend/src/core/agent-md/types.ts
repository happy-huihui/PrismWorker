/** 自定义指令（agent.md）域类型。 */

/** GET/PUT /agent-md 的响应体；content 空串表示未设置。 */
export interface AgentMdOut {
  content: string
  updated_at: number | null
  max_length: number
}

/** PUT /agent-md 的请求体。 */
export interface AgentMdSavePayload {
  content: string
}
