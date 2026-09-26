import { useEffect, useRef, useState } from 'react'
import { AtomTemplate, Loader2, MorphIcon } from '@/components/icons'
import { useAuth } from '@/app/providers/AuthProvider'
import { ApiError } from '@/core/api/client'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'

/**
 * 登录弹窗（LoginDialog）——全站唯一，开关由 AuthProvider 控制。
 *
 * 视觉（静谧纸感，frontend-design）：
 *   暖纸卡片（card 底 + 细描边 + 柔影）浮在茶褐色半透明遮罩上；
 *   右上角落一枚极低透明度的 atom 轨道线稿——和思考链头部同一个符号，
 *   让登录框看起来「生于这套界面」，而非贴上来的通用组件；
 *   标题用 Fraunces 衬线（品牌口吻），正文 Instrument Sans（工具感）；
 *   错误提示配一次 0.3s 抖动：像纸被轻敲了一下，克制但有反馈。
 */
export function LoginDialog() {
  const { loginOpen, closeLogin, login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // 错误文案复用同一节点时，用轮次计数重新触发一次抖动动画
  const [shakeTick, setShakeTick] = useState(0)
  const userRef = useRef<HTMLInputElement>(null)

  // 每次打开：清空上次的输入与错误，聚焦用户名
  useEffect(() => {
    if (loginOpen) {
      setUsername('')
      setPassword('')
      setError(null)
      setBusy(false)
      // Radix 动画完成后再聚焦，避免焦点抢跑导致滚动跳动
      const t = window.setTimeout(() => userRef.current?.focus(), 120)
      return () => window.clearTimeout(t)
    }
  }, [loginOpen])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy) return
    const u = username.trim()
    if (!u || !password) {
      failWith('请输入用户名和密码')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await login(u, password) // 成功即关闭弹窗（AuthProvider 里统一处理）
    } catch (err) {
      failWith(err instanceof ApiError ? err.detail : '登录失败，请稍后重试')
    } finally {
      setBusy(false)
    }
  }

  const failWith = (msg: string) => {
    setError(msg)
    setShakeTick((n) => n + 1)
  }

  return (
    <Dialog open={loginOpen} onOpenChange={(v) => (v ? null : closeLogin())}>
      <DialogContent
        showCloseButton={false}
        className="max-w-[380px] gap-0 overflow-hidden rounded-[18px] border-input bg-card p-0 shadow-[0_2px_4px_rgba(60,45,20,.06),0_24px_60px_rgba(60,45,20,.18)] data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95"
      >
        {/* 1.右上角 atom 轨道线稿：主题同源装饰，透明度压到几乎不可察觉 */}
        <svg
          aria-hidden
          viewBox="0 0 120 120"
          className="pointer-events-none absolute -top-7 -right-7 size-32 text-primary/[0.07]"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
        >
          <circle cx="60" cy="60" r="4" fill="currentColor" stroke="none" />
          <ellipse cx="60" cy="60" rx="52" ry="22" />
          <ellipse cx="60" cy="60" rx="52" ry="22" transform="rotate(60 60 60)" />
          <ellipse cx="60" cy="60" rx="52" ry="22" transform="rotate(120 60 60)" />
        </svg>

        <div className="flex flex-col gap-6 p-7">
          {/* 2.品牌区：P 方标 + 衬线欢迎语 */}
          <div className="flex flex-col items-center gap-3 text-center">
            <div className="grid size-10 place-items-center rounded-xl bg-primary font-display text-lg font-semibold text-primary-foreground shadow-sm">
              P
            </div>
            <div className="space-y-1">
              <DialogTitle className="font-display text-xl font-semibold tracking-tight">
                欢迎回来
              </DialogTitle>
              <DialogDescription className="text-[13px]">
                登录后继续你的会话与思考
              </DialogDescription>
            </div>
          </div>

          {/* 3.表单：用户名 / 密码 / 提交（Enter 可直达） */}
          <form onSubmit={submit} className="flex flex-col gap-3.5" noValidate>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium tracking-wide text-muted-foreground">用户名</span>
              <Input
                ref={userRef}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                placeholder="admin"
                className="h-10 rounded-[10px] border-input bg-background/60 text-sm focus-visible:border-primary/50 focus-visible:ring-primary/25"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium tracking-wide text-muted-foreground">密码</span>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                placeholder="••••••"
                className="h-10 rounded-[10px] border-input bg-background/60 text-sm focus-visible:border-primary/50 focus-visible:ring-primary/25"
              />
            </label>

            {/* 4.错误行：出现即抖一次（key 变化重挂载动画） */}
            {error && (
              <p key={shakeTick} className="animate-shake text-xs text-destructive" role="alert">
                {error}
              </p>
            )}

            <Button
              type="submit"
              disabled={busy}
              className="mt-1 h-10 w-full rounded-xl bg-primary text-sm font-medium text-primary-foreground shadow-sm transition-opacity hover:opacity-90 disabled:opacity-60"
            >
              {busy ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  验证中…
                </>
              ) : (
                '登 录'
              )}
            </Button>
          </form>

          {/* 5.脚注：演示凭据提示（本地简易系统，明示默认账号） */}
          <div className="flex items-center gap-2.5 border-t pt-4 text-[11px] text-muted-foreground/80">
            <MorphIcon icon={AtomTemplate} className="size-3.5 shrink-0 text-primary/50" />
            <span>
              默认账号 <code className="font-mono">admin / admin</code>
            </span>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
