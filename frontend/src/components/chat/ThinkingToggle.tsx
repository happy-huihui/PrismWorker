import { MorphIcon, ThinkAtom } from '@/components/icons'
import { cn } from '@/lib/utils'

/**
 * 深度思考开关（ThinkingToggle）
 *
 * 形态对齐模板 .pill.think：胶囊药丸 = 原子图标 + 「思考」，
 * 点击直接切换；开启时主色软底，关闭时中性描边。
 * 不支持的模型：置灰不可点（不是隐藏）——旧版直接 return null 把开关藏掉，
 * 但状态值还留在 localStorage 里，切到 flash 照样把 thinking_enabled=true 发出去。
 */
interface ThinkingToggleProps {
  enabled: boolean
  onChange: (enabled: boolean) => void
  /** 当前模型是否支持思考（false 时置灰禁用，并强制显示为关闭） */
  supported?: boolean
  disabled?: boolean
}

export function ThinkingToggle({ enabled, onChange, supported = true, disabled }: ThinkingToggleProps) {
  const on = supported && enabled
  const off = !supported

  return (
    <button
      type="button"
      onClick={() => supported && onChange(!enabled)}
      disabled={disabled || !supported}
      aria-pressed={on}
      title={supported ? '深度思考' : '当前模型不支持深度思考'}
      className={cn(
        'flex items-center gap-1.5 rounded-full border px-2.5 py-[5px] text-[13px] transition-colors disabled:pointer-events-none disabled:opacity-45',
        on
          ? 'border-primary/25 bg-primary/10 font-medium text-primary'
          : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground',
      )}
    >
      <MorphIcon icon={ThinkAtom} className={cn('size-[15px] shrink-0', off && 'opacity-60')} />
      <span>思考</span>
    </button>
  )
}
