import { MorphIcon, SettingsGear } from '@/components/icons'
import { useAuth } from '@/app/providers/AuthProvider'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

import { BackgroundPicker } from './BackgroundPicker'

/**
 * 侧栏底部（SidebarFooter）
 *
 * 职责：登录态用户位 + 「背景」选择器 + 「设置」入口。对齐模板 .foot：
 *      28px 圆形头像 + 13px 用户名（无副标签），右侧两枚 30px 图标钮。
 * 登录三态：
 *   自证中（booting）→ 名字位显示骨架点，避免「未登录」一闪；
 *   未登录           → 虚线头像 + 「登录」，点击弹登录框；
 *   已登录           → 实心头像 + user_id，点击出菜单（退出登录）。
 */
export function SidebarFooter({ collapsed }: { collapsed: boolean }) {
  const { userId, booting, openLogin, logout } = useAuth()

  // 1.未登录：整个用户位就是一个「登录」按钮
  const loggedOutBlock = (
    <button
      type="button"
      onClick={openLogin}
      title="登录"
      className={cn('flex min-w-0 items-center gap-2 rounded-lg px-1 py-0.5 text-left transition-colors hover:bg-secondary', collapsed && 'justify-center')}
    >
      <span className="grid size-7 shrink-0 place-items-center rounded-full border border-dashed border-input text-[13px] font-semibold text-muted-foreground">
        ?
      </span>
      {!collapsed && <span className="truncate text-[13px] font-medium text-muted-foreground">登录</span>}
    </button>
  )

  // 2.已登录：头像 + 名字 + 下拉菜单（退出）
  const loggedInBlock = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={cn('flex min-w-0 items-center gap-2 rounded-lg px-1 py-0.5 text-left transition-colors hover:bg-secondary', collapsed && 'justify-center')}
        >
          <span className="grid size-7 shrink-0 place-items-center rounded-full bg-foreground text-[13px] font-semibold text-background">
            {userId?.charAt(0).toUpperCase() ?? 'H'}
          </span>
          {!collapsed && <span className="truncate text-[13px] font-medium">{userId}</span>}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="top" className="w-44">
        <DropdownMenuLabel className="normal-case">{userId}</DropdownMenuLabel>
        <DropdownMenuItem variant="destructive" onClick={logout}>
          退出登录
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )

  // 3.自证中：占位骨架（不显示「登录」，防止状态闪烁）
  const bootingBlock = (
    <div className={cn('flex min-w-0 items-center gap-2', collapsed && 'justify-center')}>
      <span className="size-7 shrink-0 animate-pulse rounded-full bg-secondary" />
      {!collapsed && <span className="h-3 w-12 animate-pulse rounded bg-secondary" />}
    </div>
  )

  return (
    <div className={cn('flex items-center gap-2 border-t px-3 py-2.5', collapsed && 'flex-col gap-1.5 px-2')}>
      {/* 1.用户位（三态） */}
      {booting ? bootingBlock : userId ? loggedInBlock : loggedOutBlock}

      {/* 2.背景选择器（展开时把两枚图标钮推到行尾） */}
      {!collapsed && <span className="ml-auto" />}
      <BackgroundPicker collapsed={collapsed} />

      {/* 3.设置（最右，六齿齿轮） */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            title="设置"
            aria-label="设置"
            className="grid size-[30px] shrink-0 place-items-center rounded-[9px] text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            <MorphIcon icon={SettingsGear} className="size-[18px]" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" side="top" className="w-52">
          <DropdownMenuLabel>设置</DropdownMenuLabel>
          <DropdownMenuItem disabled>关于 PrismWorker · v0.1</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
