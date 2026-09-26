import { Check, ChevronDown } from '@/components/icons'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { kindIcon, kindLabel } from '@/components/artifacts/kindMeta'
import { virtualPathToFilename } from '@/core/artifacts/utils'
import { cn } from '@/lib/utils'

/**
 * 产物文件选择器（ArtifactSwitcher）
 *
 * 职责：侧栏头部的「当前文件 + 切换入口」。取代了早期的「独立清单列」——
 *      清单列会长期占掉面板 1/3 宽度，而多数会话只有 1~2 个产物，
 *      把这份空间让给预览本身更划算（与 DeerFlow 的选择一致）。
 *
 * 单产物时的处理（本项目的取舍，非照搬）：不渲染下拉，退化成一行静态标签。
 *      只有一个文件却给个能点开的箭头，是「假的可交互」，反而让人困惑。
 *
 * 字号（2026-09-23 用户反馈「顶栏太小」后放大）：
 *      文件名 13px→14px、图标 14px→16px、下拉项副标题 11px→12px。
 *      侧栏是常驻的阅读区，头部是它的标题栏，字号应与正文同级而非像脚注。
 */

interface ArtifactSwitcherProps {
  paths: string[]
  current: string | null
  onSelect: (path: string) => void
  className?: string
}

export function ArtifactSwitcher({ paths, current, onSelect, className }: ArtifactSwitcherProps) {
  const active = current ?? paths[0] ?? null
  const Icon = active ? kindIcon(active) : null

  // 空态：理论上侧栏只在有产物时打开，这里兜个底避免渲染崩
  if (!active || !Icon) {
    return (
      <span className={cn('truncate text-sm text-muted-foreground', className)}>暂无产物</span>
    )
  }

  const name = virtualPathToFilename(active)

  // 单产物：静态标签，不给假的交互入口
  if (paths.length <= 1) {
    return (
      <span className={cn('flex min-w-0 items-center gap-2', className)} title={active}>
        <span className="grid size-[30px] shrink-0 place-items-center rounded-[9px] bg-primary/10 text-primary">
          <Icon className="size-4" />
        </span>
        <span className="truncate text-[15px] font-medium">{name}</span>
      </span>
    )
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title={active}
          className={cn(
            'flex min-w-0 items-center gap-2 rounded-[10px] py-1 pr-1 pl-0.5 text-left transition-colors',
            'hover:bg-accent/60 data-[state=open]:bg-accent/60',
            className,
          )}
        >
          <span className="grid size-[30px] shrink-0 place-items-center rounded-[9px] bg-primary/10 text-primary">
            <Icon className="size-4" />
          </span>
          <span className="truncate text-[15px] font-medium">{name}</span>
          <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="start" className="max-w-[19rem] min-w-[14rem]">
        {paths.map((path) => {
          const ItemIcon = kindIcon(path)
          const isActive = path === active
          return (
            <DropdownMenuItem
              key={path}
              onSelect={() => onSelect(path)}
              className="flex items-center gap-2.5"
            >
              <ItemIcon className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm">{virtualPathToFilename(path)}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {kindLabel(path)}
                </span>
              </span>
              {isActive && <Check className="size-4 shrink-0" />}
            </DropdownMenuItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
