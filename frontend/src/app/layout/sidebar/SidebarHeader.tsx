import { PanelLeftClose, PanelLeftOpen } from '@/components/icons'
import { cn } from '@/lib/utils'

/**
 * 侧栏顶部（SidebarHeader）
 *
 * 职责：品牌标识 + 名称 + 折叠/展开切换。对齐模板 .brand：
 *      26px 主色圆角方块标记 + Fraunces 16px 品牌名，折叠按钮贴行尾（ml-auto）。
 * 图标选型：模板手绘 path 的几何重心偏左（右半是空的），视觉上像被侧栏边框压住；
 *      改用 lucide 面板图标（内容居中占满 viewBox），与底部细线图标同一家族。
 */
interface SidebarHeaderProps {
  collapsed: boolean
  onToggle: () => void
}

export function SidebarHeader({ collapsed, onToggle }: SidebarHeaderProps) {
  return (
    <div className={cn('flex items-center gap-2.5 pt-4 pr-4 pb-4 pl-4', collapsed && 'flex-col gap-3 px-2')}>
      {/* 1.品牌标记：26px 圆角方块 + 首字母（模板 .mark） */}
      <div className="grid size-[26px] shrink-0 place-items-center rounded-lg bg-primary font-display text-[15px] leading-none font-semibold text-primary-foreground">
        P
      </div>
      {!collapsed && (
        <b className="min-w-0 truncate font-display text-base font-semibold tracking-[0.2px]">PrismWorker</b>
      )}
      {/* 2.折叠/展开按钮：贴品牌行右端（模板 .collapse：30px、hover 浮现 chip 底） */}
      <button
        type="button"
        onClick={onToggle}
        aria-label={collapsed ? '展开侧栏' : '折叠侧栏'}
        title={collapsed ? '展开侧栏' : '折叠侧栏'}
        className={cn(
          'grid size-[30px] shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground',
          !collapsed && 'ml-auto',
        )}
      >
        {collapsed ? <PanelLeftOpen className="size-[18px]" /> : <PanelLeftClose className="size-[18px]" />}
      </button>
    </div>
  )
}
