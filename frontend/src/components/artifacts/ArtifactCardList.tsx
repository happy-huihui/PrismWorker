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
} from 'lucide-react'
import { toast } from 'sonner'

import { artifactKind, downloadArtifact, virtualPathToFilename } from '@/core/artifacts/utils'

const KIND_ICONS: Record<ReturnType<typeof artifactKind>, LucideIcon> = {
  image: FileImage,
  markdown: FileText,
  code: FileCode2,
  text: FileSpreadsheet,
  other: File,
}

interface ArtifactCardListProps {
  threadId: string
  /** 产物虚拟路径列表 */
  paths: string[]
  /** 点击「预览」→ 打开抽屉 */
  onPreview: (path: string) => void
}

export function ArtifactCardList({ threadId, paths, onPreview }: ArtifactCardListProps) {
  const [downloading, setDownloading] = useState<string | null>(null)

  if (paths.length === 0) return null

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
                onClick={() => onPreview(path)}
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