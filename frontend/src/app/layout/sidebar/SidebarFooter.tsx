import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Activity, MorphIcon, SettingsGear } from '@/components/icons'
import { useAuth } from '@/app/providers/AuthProvider'

import { cn } from '@/lib/utils'

import { SettingsDialog } from '../settings/SettingsDialog'

/**
 * 侧栏底部（SidebarFooter）
 *
 * 职责：登录态用户位 + 「设置」入口。对齐主流 Agent 的做法——整行可点，
 *      点击打开设置弹窗（齿轮仅作行尾视觉件）；未登录时点击引导登录。
 * 登录三态：
 *   自证中（booting）→ 名字位显示骨架点，且整行禁点，避免「未登录」一闪；
 *   未登录           → 虚线头像 + 「登录」，点击弹登录框；
 *   已登录           → 实心头像 + user_id，点击进设置（退出登录在「账户设置」里）。
 */
export function SidebarFooter({ collapsed }: { collapsed: boolean }) {
  const { userId, booting, openLogin, isAdmin } = useAuth()
  const navigate = useNavigate()
  // 设置弹窗开合（本地状态即可，无需全局）
  const [settingsOpen, setSettingsOpen] = useState(false)

  // 打开设置：未登录先引导登录
  const handleOpen = () => (userId ? setSettingsOpen(true) : openLogin())

  // 用户位（自证中/未登录/已登录三态，均纯展示，点击行为统一由整行承担）
  const userBlock = booting ? (
    // 自证中：占位骨架（不显示「登录」，防止状态闪烁）
    <div className={cn('flex min-w-0 items-center gap-2', collapsed && 'justify-center')}>
      <span className="size-7 shrink-0 animate-pulse rounded-full bg-secondary" />
      {!collapsed && <span className="h-3 w-12 animate-pulse rounded bg-secondary" />}
    </div>
  ) : (
    <div className={cn('flex min-w-0 items-center gap-2', collapsed && 'justify-center')}>
      <span
        className={cn(
          'grid size-7 shrink-0 place-items-center rounded-full text-[13px] font-semibold',
          userId
            ? 'bg-foreground text-background'
            : 'border border-dashed border-input text-muted-foreground',
        )}
      >
        {userId ? userId.charAt(0).toUpperCase() : '?'}
      </span>
      {!collapsed && (
        <span className={cn('truncate text-[13px] font-medium', !userId && 'text-muted-foreground')}>
          {userId ?? '登录'}
        </span>
      )}
    </div>
  )

  return (
    <div className={cn('border-t px-2 py-2', collapsed && 'px-1.5')}>
      {/* 观测台入口：仅管理员可见，进入 /observability（独立整页中台） */}
      {isAdmin && (
        <button
          type="button"
          title="观测台"
          aria-label="观测台"
          onClick={() => navigate('/observability')}
          className={cn(
            'flex w-full items-center gap-2 rounded-lg px-1.5 py-1 text-left text-[13px] text-muted-foreground transition-colors',
            'hover:bg-secondary hover:text-foreground',
            collapsed && 'justify-center px-0',
          )}
        >
          <span className="grid size-[30px] shrink-0 place-items-center rounded-[9px]">
            <Activity className="size-[18px]" />
          </span>
          {!collapsed && <span>观测台</span>}
        </button>
      )}

      {/* 整行可点：进入设置（齿轮为行尾视觉件，不单独响应） */}
      <button
        type="button"
        title="设置"
        aria-label="设置"
        onClick={handleOpen}
        disabled={booting}
        className={cn(
          'flex w-full items-center gap-2 rounded-lg px-1.5 py-1 text-left transition-colors',
          'hover:bg-secondary disabled:pointer-events-none',
          collapsed && 'flex-col justify-center gap-1.5',
        )}
      >
        {/* 1.用户位（三态） */}
        {userBlock}

        {/* 2.设置齿轮（展开时推到行尾） */}
        {!collapsed && <span className="ml-auto" />}
        <span className="grid size-[30px] shrink-0 place-items-center rounded-[9px] text-muted-foreground">
          <MorphIcon icon={SettingsGear} className="size-[18px]" />
        </span>
      </button>

      {/* 3.设置弹窗（账户/外观/自定义指令） */}
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  )
}
