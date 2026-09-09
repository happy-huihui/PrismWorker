import { AlertCircle, CheckCircle2, XCircle } from 'lucide-react'

import { type RunStreamStatus } from '@/core/runs/useRunStream'

interface RunStatusBarProps {
  status: RunStreamStatus
  messageCount: number | null
  error: string | null
  startedAt: number | null
  finishedAt: number | null
}

function fmtDuration(startedAt: number | null, finishedAt: number | null): string | null {
  if (startedAt == null || finishedAt == null) return null
  const d = Math.max(0, finishedAt - startedAt)
  return d < 60 ? `${d.toFixed(1)} 秒` : `${Math.floor(d / 60)} 分 ${(d % 60).toFixed(0)} 秒`
}

export function RunStatusBar({ status, messageCount, error, startedAt, finishedAt }: RunStatusBarProps) {
  if (status === 'finished') {
    const dur = fmtDuration(startedAt, finishedAt)
    return (
      <div className="my-1 flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="size-3.5 shrink-0" />
        <span>
          运行完成{messageCount != null && ` · ${messageCount} 条消息`}
          {dur && ` · 耗时 ${dur}`}
        </span>
      </div>
    )
  }

  if (status === 'error' && error) {
    return (
      <div className="my-1 flex items-start gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-400">
        <XCircle className="mt-0.5 size-3.5 shrink-0" />
        <span className="min-w-0 break-words">
          <span className="font-medium">运行失败：</span>
          {error}
        </span>
      </div>
    )
  }

  if (status === 'cancelled') {
    return (
      <div className="my-1 flex items-center gap-1.5 text-xs text-muted-foreground">
        <AlertCircle className="size-3.5 shrink-0" />
        <span>已停止</span>
      </div>
    )
  }

  return null
}