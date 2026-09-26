import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { clearToken, getToken, meRequest, loginRequest } from '@/core/auth'

/**
 * 登录态（AuthProvider）——全站唯一的「谁在用」状态源。
 *
 * 职责：
 *   1. 启动自证：本地有 token → 调 /auth/me 换回 user_id（token 被后端判失效
 *      时，client.ts 会清 token 并广播事件，这里统一响应）；
 *   2. 登录/登出：包装 core/auth 接口，成功后维护 userId 状态；
 *   3. 登录弹窗开关：openLogin/closeLogin 全局可达——左下角入口、发消息拦截、
 *      401 广播都汇聚到同一个 LoginDialog，避免多处各管一个弹窗。
 *
 * 状态判定：userId 非空 = 已登录；token 只是凭证，界面一律以 userId 为准。
 */

interface AuthContextValue {
  /** 当前登录用户 id（null = 未登录） */
  userId: string | null
  /** 启动自证是否进行中（避免闪烁：期间不渲染「未登录」态入口） */
  booting: boolean
  /** 登录弹窗是否打开 */
  loginOpen: boolean
  openLogin: () => void
  closeLogin: () => void
  /** 登录：成功返回 true；失败返回 false（错误文案由弹窗自己展示） */
  login: (username: string, password: string) => Promise<boolean>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [userId, setUserId] = useState<string | null>(null)
  const [booting, setBooting] = useState<boolean>(() => !!getToken())
  const [loginOpen, setLoginOpen] = useState(false)

  // 1.启动自证：有 token 就问后端「我还有效吗」；无效路径由 client.ts 401 拦截兜底
  useEffect(() => {
    if (!getToken()) return
    let cancelled = false
    meRequest()
      .then((me) => {
        if (!cancelled) setUserId(me.user_id)
      })
      .catch(() => {
        // 401 时 client.ts 已清 token 并广播事件；这里只需收尾 booting
      })
      .finally(() => {
        if (!cancelled) setBooting(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 2.全局失效事件：任何请求撞回 401 → 打回未登录并弹框（token 过期/服务端重启丢密钥）
  useEffect(() => {
    const onUnauthorized = () => {
      setUserId(null)
      setLoginOpen(true)
    }
    window.addEventListener('prism:unauthorized', onUnauthorized)
    return () => window.removeEventListener('prism:unauthorized', onUnauthorized)
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const res = await loginRequest(username, password)
    setUserId(res.user_id)
    setLoginOpen(false)
    return true
  }, [])

  const logout = useCallback(() => {
    clearToken()
    setUserId(null)
  }, [])

  const openLogin = useCallback(() => setLoginOpen(true), [])
  const closeLogin = useCallback(() => setLoginOpen(false), [])

  const value = useMemo(
    () => ({ userId, booting, loginOpen, openLogin, closeLogin, login, logout }),
    [userId, booting, loginOpen, openLogin, closeLogin, login, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/** 读取登录态与操作（必须在 <AuthProvider> 内使用）。 */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth 必须在 <AuthProvider> 内使用')
  return ctx
}
