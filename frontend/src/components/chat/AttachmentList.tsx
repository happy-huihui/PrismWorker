import {
  File,
  FileCode2,
  FileImage,
  FileSpreadsheet,
  FileText,
  Loader2,
  RotateCcw,
  X,
  type LucideIcon,
} from 'lucide-react'

import { formatBytes } from '@/lib/format'
import { cn } from '@/lib/utils'
import { artifactKind } from '@/core/artifacts/utils'

export interface AttachmentItem {
  /** 唯一键（上传中=本地时间戳；成功后=virtual_path） */
  key: string
  name: string
  size: number
  status: 'uploading' | 'done' | 'error'
  /** 成功后回填的沙箱虚拟路径 */
  virtualPath?: string
  error?: string
  /** 本地文件引用（失败重试用；成功/移除后不再需要） */
  file?: File
}

const KIND_ICONS: Record<ReturnType<typeof artifactKind>, LucideIcon> = {
  image: FileImage,
  markdown: FileText,
  code: FileCode2,
  text: FileSpreadsheet,
  other: File,
}

interface AttachmentListProps {
  items: AttachmentItem[]
  /** 移除单项（成功项仅移出列表，后端文件保留供模型读取） */
  onRemove?: (item: AttachmentItem) => void
  /** 失败项重试 */
  onRetry?: (item: AttachmentItem) => void
}

export function AttachmentList({ items, onRemove, onRetry }: AttachmentListProps) {
  if (items.length === 0) return null

  return (
    <div className="flex shrink-0 flex-wrap gap-2 px-4">
      {items.map((item) => (
        <RichFileCard key={item.key} item={item} onRemove={onRemove} onRetry={onRetry} />
      ))}
    </div>
  )
}

function RichFileCard({
  item,
  onRemove,
  onRetry,
}: {
  item: AttachmentItem
  onRemove?: (item: AttachmentItem) => void
  onRetry?: (item: AttachmentItem) => void
}) {
  const Icon = KIND_ICONS[artifactKind(item.name)]
  const failed = item.status === 'error'

  return (
    <div
      className={cn(
        'relative flex max-w-64 items-center gap-2 rounded-lg border bg-card px-3 py-1.5 text-sm',
        failed
          ? 'border-red-200 bg-red-50/50 dark:border-red-500/30 dark:bg-red-500/10'
          : item.status === 'uploading'
            ? 'opacity-70'
            : '',
      )}
      title={item.virtualPath ? `已上传：${item.virtualPath}` : item.error ?? item.name}
    >
      <Icon className="size-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate">{item.name}</span>

      {item.status === 'uploading' && (
        <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
          <Loader2 className="size-3 animate-spin" />
          上传中…
        </span>
      )}
      {item.status === 'done' && (
        <span className="shrink-0 text-xs text-muted-foreground">{formatBytes(item.size)}</span>
      )}
      {failed && (
        <span className="shrink-0 text-xs text-red-500">失败</span>
      )}

      <span className="flex shrink-0 items-center gap-0.5">
        {failed && onRetry && (
          <button
            type="button"
            onClick={() => onRetry(item)}
            className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            title="重试"
          >
            <RotateCcw className="size-3.5" />
          </button>
        )}
        {onRemove && (
          <button
            type="button"
            onClick={() => onRemove(item)}
            className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            title="移除"
          >
            <X className="size-3.5" />
          </button>
        )}
      </span>
    </div>
  )
}