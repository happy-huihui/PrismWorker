import { NavLink, Outlet, useNavigate } from 'react-router-dom'

import { ArrowLeft } from '@/components/icons'
import { useAuth } from '@/app/providers/AuthProvider'
import { cn } from '@/lib/utils'

/**
 * 观测台独立布局（app/layout/ObservabilityLayout）
 *
 * 职责：观测中台自己的整页外壳——左侧导航（总览/运行记录/分析）+ 品牌 + 管理员位 +
 *       返回对话，右侧渲染子页（Outlet）。**不嵌在 AppLayout 聊天壳里**，管理员点
 *       观测台直达这里，全屏沉浸。
 */

const NAV_ITEMS = [
  { to: '/observability', label: '总览', icon: '◈', end: true },
  { to: '/observability/runs', label: '运行记录', icon: '☰', end: false },
  { to: '/observability/analytics', label: '分析', icon: '◧', end: false },
  { to: '/observability/evals', label: '评测', icon: '★', end: false },
]

export function ObservabilityLayout() {
  const navigate = useNavigate()
  const { userId } = useAuth()

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background text-foreground">
      {/* 左导航 */}
      <aside className="flex w-[216px] shrink-0 flex-col border-r bg-sidebar px-3 pb-3.5 pt-[18px]">
        <div className="flex items-baseline gap-2 px-2.5 pb-5">
          <span className="font-display text-[17px] font-semibold">PrismWorker</span>
          <span className="rounded-full bg-primary px-[7px] py-px text-[11px] font-semibold text-primary-foreground">
            观测台
          </span>
        </div>

        <div className="px-2.5 pb-1.5 text-[11px] tracking-wide text-muted-foreground">观测</div>
        {NAV_ITEMS.map((it) => (
          <NavLink
            key={it.to}
            to={it.to}
            end={it.end}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] transition-colors',
                isActive
                  ? 'bg-secondary font-semibold text-foreground'
                  : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
              )
            }
          >
            <span className="w-4 text-center opacity-85">{it.icon}</span>
            {it.label}
          </NavLink>
        ))}

        {/* 底部：管理员位 + 返回对话 */}
        <div className="mt-auto border-t border-border pt-3">
          <div className="flex items-center gap-2 px-2.5 py-2 text-[12.5px] text-muted-foreground">
            <span className="size-[7px] rounded-full bg-success" />
            管理员 · {userId ?? '…'}
            <span className="ml-auto text-[10.5px] opacity-70">全用户数据</span>
          </div>
          <button
            type="button"
            onClick={() => navigate('/')}
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-[12.5px] text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" />
            返回对话
          </button>
        </div>
      </aside>

      {/* 右侧子页 */}
      <main className="min-w-0 flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
