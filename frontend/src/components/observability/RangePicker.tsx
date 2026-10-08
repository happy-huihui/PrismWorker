import { cn } from '@/lib/utils'

/**
 * 时间窗口选择器（components/observability/RangePicker）
 *
 * 职责：总览 / 分析页共用的「今天 / 近 7 天 / 近 30 天」切换，值即后端 range 参数。
 */

const RANGES = [
  { value: 'today', label: '今天' },
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
]

export function RangePicker({
  value,
  onChange,
}: {
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="flex gap-1">
      {RANGES.map((r) => (
        <button
          key={r.value}
          type="button"
          onClick={() => onChange(r.value)}
          className={cn(
            'rounded-md px-2.5 py-1 text-[13px] transition-colors',
            value === r.value
              ? 'bg-primary text-primary-foreground'
              : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
          )}
        >
          {r.label}
        </button>
      ))}
    </div>
  )
}
