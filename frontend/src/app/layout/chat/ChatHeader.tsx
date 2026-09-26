import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { formatRelativeTime } from '@/lib/format'

/**
 * 对话顶栏（ChatHeader）
 *
 * 职责：会话区顶部——标题 + 「PrismWorker · 相对时间」副标题 + 运行状态徽标。
 *      对齐模板 .chead：28px 水平内边距、Fraunces 17px 标题、12px 副标题。
 */
interface ChatHeaderProps {
  title?: string
  /** 标题加载中：显示骨架 */
  loading?: boolean
  /** 会话更新时间（秒时间戳），用于副标题「X 前」 */
  updatedAt?: number
  /** 本轮是否运行中 */
  active?: boolean
}

export function ChatHeader({ title, loading, updatedAt, active }: ChatHeaderProps) {
  return (
    <header className="flex shrink-0 items-center gap-2.5 border-b px-7 py-4">
      {loading ? (
        <Skeleton className="h-5 w-40" />
      ) : (
        <>
          <h1 className="min-w-0 truncate font-display text-[17px] font-semibold">{title ?? '会话'}</h1>
          {updatedAt != null && (
            <span className="shrink-0 text-xs text-muted-foreground">
              PrismWorker · {formatRelativeTime(updatedAt)}
            </span>
          )}
          {active && (
            <Badge variant="secondary" className="shrink-0 text-xs">
              运行中
            </Badge>
          )}
        </>
      )}
    </header>
  )
}
