// auth 域类型：与后端 app/api/routes/auth.py 的模型一一对应

/** POST /auth/login 响应：token 供持久化，user_id 供展示身份，is_admin 供显隐观测台入口 */
export interface LoginResponse {
  token: string
  user_id: string
  is_admin?: boolean
}

/** GET /auth/me 响应：token 有效性自查 */
export interface MeResponse {
  user_id: string
  is_admin?: boolean
}
