import { useEffect, useRef, useState } from 'react'
import { Download, FileQuestion, Loader2, TriangleAlert } from 'lucide-react'
import { toast } from 'sonner'

import { MarkdownContent } from '@/components/chat/MarkdownContent'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  artifactKind,
  downloadArtifact,
  fetchArtifact,
  parseArtifactError,
  virtualPathToFilename,
} from '@/core/artifacts/utils'

type PreviewView =
  | { kind: 'loading' }
  | { kind: 'error'; msg: string }
  | { kind: 'image'; url: string }
  | { kind: 'text'; text: string; lang: string; isMarkdown: boolean }
  | { kind: 'other' }

function langFromPath(path: string): string {
  const m = /\.([A-Za-z0-9_+-]+)$/.exec(path)
  return m ? m[1].toLowerCase() : ''
}

interface ArtifactPreviewDrawerProps {
  threadId: string
  /** 当前预览的产物虚拟路径（null = 无选中） */
  path: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ArtifactPreviewDrawer({ threadId, path, open, onOpenChange }: ArtifactPreviewDrawerProps) {
  const [view, setView] = useState<PreviewView>({ kind: 'loading' })
  const [downloading, setDownloading] = useState(false)
  const objectUrlRef = useRef<string | null>(null)

  useEffect(() => {
    if (!open || !path) {
      setView({ kind: 'loading' })
      return
    }
    const ctl = new AbortController()
    setView({ kind: 'loading' })

    void (async () => {
      try {
        const resp = await fetchArtifact(threadId, path, ctl.signal)
        if (!resp.ok) {
          setView({ kind: 'error', msg: parseArtifactError(resp) })
          return
        }
        const kind = artifactKind(path)
        if (kind === 'image') {
          const blob = await resp.blob()
          if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current)
          const url = URL.createObjectURL(blob)
          objectUrlRef.current = url
          setView({ kind: 'image', url })
        } else if (kind === 'markdown') {
          setView({ kind: 'text', text: await resp.text(), lang: 'md', isMarkdown: true })
        } else if (kind === 'code' || kind === 'text') {
          const lang = kind === 'code' ? langFromPath(path) : 'text'
          setView({ kind: 'text', text: await resp.text(), lang, isMarkdown: false })
        } else {
          setView({ kind: 'other' })
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setView({
          kind: 'error',
          msg: err instanceof Error && err.message ? err.message : '产物加载失败',
        })
      }
    })()

    return () => ctl.abort()
  }, [open, path, threadId])

  useEffect(() => {
    if (!open) {
      const url = objectUrlRef.current
      if (url) {
        URL.revokeObjectURL(url)
        objectUrlRef.current = null
      }
    }
  }, [open])

  const handleDownload = async () => {
    if (!path || downloading) return
    setDownloading(true)
    try {
      await downloadArtifact(threadId, path)
    } catch (err) {
      toast.error('下载失败', {
        description: err instanceof Error ? err.message : '未知错误',
      })
    } finally {
      setDownloading(false)
    }
  }

  const filename = path ? virtualPathToFilename(path) : ''

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[90%] gap-0 p-0 sm:max-w-2xl">
        <SheetHeader className="flex-row items-center justify-between border-b px-4 py-3 sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1">
            <SheetTitle className="truncate text-sm">{filename || '产物预览'}</SheetTitle>
            <SheetDescription className="hidden" />
          </div>
          <Button
            size="sm"
            variant="outline"
            className="shrink-0 gap-1.5 text-xs"
            disabled={!path || downloading || view.kind === 'error'}
            onClick={() => void handleDownload()}
          >
            {downloading ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
            下载
          </Button>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {view.kind === 'loading' && (
            <div className="space-y-2">
              <Skeleton className="h-4 w-1/3" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          )}

          {view.kind === 'error' && (
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <TriangleAlert className="size-8 text-amber-500" />
              <p className="text-sm font-medium">{view.msg}</p>
              <p className="max-w-xs text-xs text-muted-foreground">
                产物可能已被移动、删除，或路径越界（后端已拒绝）。
              </p>
            </div>
          )}

          {view.kind === 'image' && (
            <img
              src={view.url}
              alt={filename}
              className="mx-auto max-h-[70vh] max-w-full rounded-lg border"
            />
          )}

          {view.kind === 'text' && view.isMarkdown && (
            <MarkdownContent text={view.text} />
          )}

          {view.kind === 'text' && !view.isMarkdown && (
            <MarkdownContent text={`\`\`\`${view.lang}\n${view.text}\n\`\`\``} />
          )}

          {view.kind === 'other' && (
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <FileQuestion className="size-8 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                该文件类型（二进制/未知）暂不支持内联预览
              </p>
              <p className="text-xs text-muted-foreground/70">请使用右上角「下载」获取原件。</p>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}