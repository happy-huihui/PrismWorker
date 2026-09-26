import { api } from '@/core/api/client'

import { setToken } from './token'
import { type LoginResponse, type MeResponse } from './types'

/**
 * auth 数据层：登录/自查接口。
 *
 * 约定：token 的读写在 ./token.ts（叶子模块，避免与 client.ts 互相 import 成环）；
 *      请求发送与 401 拦截由 core/api/client.ts 统一负责，这里不重复。
 */

/** 登录：验密换 token（成功即落盘）。失败抛 ApiError（detail 为后端提示语）。 */
export async function loginRequest(username: string, password: string): Promise<LoginResponse> {
  const res = await api.post<LoginResponse>('/auth/login', { username, password })
  setToken(res.token)
  return res
}

/** 自查：拿现有 token 换 user_id；401 表示登录已失效（client 层会广播事件）。 */
export async function meRequest(): Promise<MeResponse> {
  return api.get<MeResponse>('/auth/me')
}
