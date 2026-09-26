/**
 * token 本地存取（token）——auth 域最底层的零依赖小模块。
 *
 * 为什么单独成文件：core/api/client.ts（发请求要读 token、401 要清 token）
 * 与 core/auth/api.ts（登录要写 token）互相需要对方，直接 import 会成环；
 * 把「token 放哪、怎么读写」抽到这里，两边都只依赖这个叶子模块。
 */

const TOKEN_KEY = 'prism-token'

/** 401 时向全局广播的事件名（AuthProvider 监听 → 弹登录框）。 */
export const UNAUTHORIZED_EVENT = 'prism:unauthorized'

/** 读本地 token（无 = 未登录）。localStorage 不可用时按未登录处理。 */
export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

/** 写 token（登录成功时调用）。 */
export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token)
  } catch {
  }
}

/** 清 token（登出 / 401 失效时调用）。 */
export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY)
  } catch {
  }
}

/** 拼 Authorization 头的值；无 token 返回 null（调用方自行跳过设置）。 */
export function authHeader(): string | null {
  const token = getToken()
  return token ? `Bearer ${token}` : null
}

/** 给裸 fetch（SSE 流/产物下载）用的请求头包：有 token 才带 Authorization。 */
export function authHeaders(): Record<string, string> {
  const auth = authHeader()
  return auth ? { Authorization: auth } : {}
}
