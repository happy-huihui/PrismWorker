import type { LogOut } from '@/core/observability/types'

import { cn } from '@/lib/utils'

import { formatClock } from './format'

/**
 * 日志流（components/observability/LogStream）
 *
 * 职责：把结构化日志按时间正序渲染成等宽日志行——短时钟 + 级别着色 + event 动词 +
 *       附加字段（单行 ellipsis）。行自带内边距，外层容器无需再 padding（对齐模板）。
 */

const LEVEL_COLOR: Record<string, string> = {
  debug: 'text-muted-foreground',
  info: 'text-foreground',
  warning: 'text-amber-600',
  error: 'text-destructive',
  critical: 'text-destructive',
}

export function LogStream({ logs }: { logs: LogOut[] }) {
  if (logs.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">暂无日志</p>
  }
  return (
    <div className="overflow-x-auto font-mono text-xs leading-relaxed">
      {logs.map((log) => {
        const fields = Object.keys(log.fields ?? {}).length ? JSON.stringify(log.fields) : ''
        return (
          <div
            key={log.log_id}
            className="flex items-baseline gap-2.5 border-b border-border/40 px-3 py-[5px] last:border-b-0"
          >
            <span className="shrink-0 tabular-nums text-muted-foreground">{formatClock(log.timestamp)}</span>
            <span className={cn('w-14 shrink-0 font-semibold', LEVEL_COLOR[log.level] ?? 'text-foreground')}>
              {log.level.toUpperCase()}
            </span>
            <span className="shrink-0 font-medium">{log.event}</span>
            {fields && <span className="min-w-0 flex-1 truncate text-muted-foreground">{fields}</span>}
          </div>
        )
      })}
    </div>
  )
}
