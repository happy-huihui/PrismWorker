import { useEffect, useMemo, useRef, useState } from 'react'
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
} from '@/components/icons'

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
  /** 本地文件引用（失败重试用；也用于生成图片预览 URL） */
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

/**
 * 附件列表（AttachmentList）
 *
 * 布局：容器与输入框/对话区**同宽居中**（`mx-auto max-w-[820px]`），
 *      这样粘贴的图片卡片左边缘与输入框左边缘严格对齐。
 *      旧版只有 `px-4`，于是贴在整个页面左边缘，视觉上「跑偏」了。
 *
 * 图片附件：渲染真实缩略图（`URL.createObjectURL`），鼠标悬停弹出大图预览
 *      （WorkBuddy 风格：缩略图悬停后在上方浮出大图 + 文件名 + 大小）。
 *      非图片仍走文件类型图标卡片。
 */
export function AttachmentList({ items, onRemove, onRetry }: AttachmentListProps) {
  if (items.length === 0) return null

  return (
    <div className="shrink-0 px-7 pb-1">
      <div className="mx-auto flex w-full max-w-[820px] flex-wrap items-center gap-2">
        {items.map((item) => (
          <RichFileCard key={item.key} item={item} onRemove={onRemove} onRetry={onRetry} />
        ))}
      </div>
    </div>
  )
}

/** 生成（并在卸载时回收）本地图片预览 URL；非图片返回 null。 */
function useObjectUrl(file: File | undefined, enabled: boolean): string | null {
  const url = useMemo(() => {
    if (!enabled || !file) return null
    try {
      return URL.createObjectURL(file)
    } catch {
      return null
    }
  }, [file, enabled])

  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url)
    }
  }, [url])

  return url
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
  const kind = artifactKind(item.name)
  const isImage = kind === 'image'
  const previewUrl = useObjectUrl(item.file, isImage)
  const Icon = KIND_ICONS[kind]
  const failed = item.status === 'error'

  // 悬停预览：鼠标进入后延迟一点再开（避免扫过时闪一下）
  const [hovered, setHovered] = useState(false)
  const openTimer = useRef<number | null>(null)
  const canPreview = isImage && !!previewUrl && !failed

  const handleEnter = () => {
    if (!canPreview) return
    openTimer.current = window.setTimeout(() => setHovered(true), 140)
  }
  const handleLeave = () => {
    if (openTimer.current) {
      window.clearTimeout(openTimer.current)
      openTimer.current = null
    }
    setHovered(false)
  }
  useEffect(() => {
    return () => {
      if (openTimer.current) window.clearTimeout(openTimer.current)
    }
  }, [])

  return (
    <div
      className="relative"
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
    >
      <div
        className={cn(
          'relative flex max-w-64 items-center gap-2 rounded-lg border bg-card px-2 py-1.5 text-sm',
          failed
            ? 'border-red-200 bg-red-50/50 dark:border-red-500/30 dark:bg-red-500/10'
            : item.status === 'uploading'
              ? 'opacity-70'
              : '',
        )}
        title={item.virtualPath ? `已上传：${item.virtualPath}` : item.error ?? item.name}
      >
        {/* 1.缩略图槽：图片显示真实缩略图，其余显示文件类型图标 */}
        <span className="relative grid size-5 shrink-0 place-items-center overflow-hidden rounded">
          {isImage && previewUrl ? (
            <img src={previewUrl} alt={item.name} className="size-5 object-cover" />
          ) : (
            <Icon className="size-4 text-muted-foreground" />
          )}
        </span>
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
        {failed && <span className="shrink-0 text-xs text-red-500">失败</span>}

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

      {/* 2.悬停大图预览：浮在卡片上方，含文件名与大小 */}
      {hovered && previewUrl && (
        <div
          className="pointer-events-none absolute bottom-[calc(100%+8px)] left-0 z-50 w-64 animate-message-in rounded-xl border bg-popover p-2 shadow-xl"
          role="tooltip"
        >
          <div className="flex max-h-72 w-full items-center justify-center overflow-hidden rounded-lg bg-muted/40">
            <img
              src={previewUrl}
              alt={item.name}
              className="max-h-72 w-full object-contain"
            />
          </div>
          <div className="mt-2 flex items-baseline gap-2 px-0.5">
            <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
              {item.name}
            </span>
            <span className="shrink-0 text-[11px] text-muted-foreground">
              {formatBytes(item.size)}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
