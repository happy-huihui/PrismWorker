import { useState } from 'react'
import {
  Download,
  Eye,
  File,
  FileCode2,
  FileImage,
  FileSpreadsheet,
  FileText,
  Loader2,
  PackageOpen,
  type LucideIcon,
} from '@/components/icons'
import { toast } from 'sonner'

import { useArtifactsOptional } from '@/core/artifacts/context'
import { artifactKind, downloadArtifact, virtualPathToFilename } from '@/core/artifacts/utils'

const KIND_ICONS: Record<ReturnType<typeof artifactKind>, LucideIcon> = {
  image: FileImage,
  markdown: FileText,
  code: FileCode2,
  text: FileSpreadsheet,
  other: File,
}

/**
 * 产物卡片列表（ArtifactCardList）
 *
 * 职责：在消息流底部列出本轮交付的产物，点击「预览」唤起右侧产物侧边栏。
 *
 * 为什么优先用 Context 而不是 props 传 onPreview：
 *      本组件位于 MessageList → MessageBubble 深层子树，而侧边栏状态
 *      属于页面级；继续透传 props 会让整条消息渲染链都背上这个参数。
 *      因此这里先尝试 useArtifactsOptional()，拿不到时再回退到 onPreview
 *      （欢迎页等未挂 Provider 的场景复用本组件时用得上）。
 */
interface ArtifactCardListProps {
  threadId: string
  /** 产物虚拟路径列表 */
  paths: string[]
  /** 回退的预览回调（无 ArtifactsProvider 时使用） */
  onPreview?: (path: string) => void
}

export function ArtifactCardList({ threadId, paths, onPreview }: ArtifactCardListProps) {
  const [downloading, setDownloading] = useState<string | null>(null)
  const artifactsCtx = useArtifactsOptional()

  if (paths.length === 0) return null

  /** 打开预览：优先走侧边栏 Context，其次回退到父级回调。 */
  const openPreview = (path: string) => {
    if (artifactsCtx) {
      artifactsCtx.select(path)
      return
    }
    onPreview?.(path)
  }

  const handleDownload = async (path: string) => {
    if (downloading) return
    setDownloading(path)
    try {
      await downloadArtifact(threadId, path)
    } catch (err) {
      toast.error('下载失败', {
        description: err instanceof Error ? err.message : '未知错误',
      })
    } finally {
      setDownloading(null)
    }
  }

  return (
    <div className="my-1.5 flex flex-col gap-1.5 animate-message-in">
      <div className="flex items-center gap-1.5 px-0.5">
        <PackageOpen className="size-3.5 text-muted-foreground" />
        <p className="text-xs font-medium text-muted-foreground">交付产物</p>
        <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground/70">
          {paths.length} 个文件
        </span>
      </div>

      <div className="flex flex-col gap-1.5">
        {paths.map((path, i) => {
          const Icon = KIND_ICONS[artifactKind(path)]
          const name = virtualPathToFilename(path)
          return (
            <div
              key={`${path}-${i}`}
              className="group flex items-center gap-2 rounded-xl border bg-card/60 px-3 py-2 transition-colors hover:bg-muted/50"
            >
              <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground transition-colors group-hover:bg-background">
                <Icon className="size-3.5" />
              </span>
              <span className="min-w-0 flex-1 truncate text-sm" title={path}>
                {name}
              </span>

              <button
                type="button"
                onClick={() => openPreview(path)}
                className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                title="预览"
              >
                <Eye className="size-3.5" />
                预览
              </button>

              <button
                type="button"
                onClick={() => void handleDownload(path)}
                disabled={downloading != null}
                className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
                title="下载"
              >
                {downloading === path ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Download className="size-3.5" />
                )}
                下载
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}