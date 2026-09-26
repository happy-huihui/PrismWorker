import { Check, MorphIcon, PaletteLine } from '@/components/icons'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { BACKGROUNDS, useBackground, type BackgroundKey } from '@/app/providers/BackgroundProvider'
import { cn } from '@/lib/utils'

/**
 * 背景选择器（BackgroundPicker）
 *
 * 职责：侧栏底部「背景」按钮 + 向上弹出浮层，列出三套背景（色板预览 + 名称 + 说明），
 *      选中即切换并持久化（由 BackgroundProvider 负责）。取代旧的 light/dark 开关。
 */

// 每个背景的小色板预览（与 index.css 三套主色一致）
const SWATCH: Record<BackgroundKey, string> = {
  paper: 'linear-gradient(135deg,#f3efe7,#efe4d2)',
  glass: 'linear-gradient(135deg,#0a0d14,#123 60%,#0a0d14)',
  mist: 'linear-gradient(135deg,#ffd9c2,#cfe9ff 50%,#cdf0d8)',
}

export function BackgroundPicker({ collapsed }: { collapsed: boolean }) {
  const { background, setBackground } = useBackground()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title="背景"
          aria-label="选择背景"
          className={cn(
            // 模板 .iconbtn：30px、9px 圆角；激活（浮层打开）时主色软底
            'grid size-[30px] shrink-0 place-items-center rounded-[9px] text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground data-[state=open]:bg-primary/10 data-[state=open]:text-primary',
            collapsed && 'mx-auto',
          )}
        >
          <MorphIcon icon={PaletteLine} className="size-[18px]" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="top" className="w-60">
        {/* 顶部说明 */}
        <div className="px-2 py-1.5 text-[11px] tracking-wide text-muted-foreground uppercase">选择背景</div>
        {BACKGROUNDS.map((b) => (
          <DropdownMenuItem key={b.key} onClick={() => setBackground(b.key)} className="gap-2.5 py-2">
            {/* 1.色板预览 */}
            <span className="size-6 shrink-0 rounded-md border" style={{ background: SWATCH[b.key] }} />
            <span className="flex min-w-0 flex-col">
              <span className="truncate text-sm">{b.name}</span>
              <span className="truncate text-[11px] text-muted-foreground">{b.desc}</span>
            </span>
            {/* 2.当前项打勾 */}
            {background === b.key && <Check className="ml-auto size-4 text-primary" />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
