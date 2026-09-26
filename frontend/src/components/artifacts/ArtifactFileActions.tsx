import { useState } from 'react'
import { Code2, Copy, Download, Eye, Loader2, Maximize2 } from '@/components/icons'

import { downloadArtifact } from '@/core/artifacts/utils'
import type { ArtifactContent } from '@/core/artifacts/hooks'
import { cn } from '@/lib/utils'
import { toast } from 'sonner'

/**
 * 产物操作按钮组（ArtifactFileActions）
 *
 * 职责：侧栏头部右侧的一排操作 —— 网页的「预览/源码」切换（带文字标签）、
 *       新标签页打开、复制、下载。
 *
 * 为什么从 ArtifactFileDetail 的头部抽出来：
 *      改造后侧栏是「单行头部 + 正文」，头部同时承载文件选择器与操作按钮，
 *      而正文渲染不应再自带一条头部（否则两行 chrome 挤掉预览高度）。
 *      因此把「操作」与「正文」拆成两个组件，由 ArtifactSidePanel 组合。
 *
 * 视觉规格（2026-09-25 用户定稿「方案 A」）：
 *      - 分段控件 = 圆角胶囊 + **带文字标签**（`代码` / `预览`，不再靠图标猜），
 *        激活态主色填充（对齐 DeerFlow 的 `</> 代码 | 预览` 分段），
 *        图标 15px、文字 13px、胶囊高 32px。
 *      - 图标按钮 34px（hover 有底 + 图标加亮），下载/复制/新标签页三枚为一组，
 *        由细分隔线与「收起」隔开。
 *
 * 图标按钮一律配 title（与侧栏其它按钮一致），不额外引入 Tooltip Provider。
 */

interface ArtifactFileActionsProps {
  threadId: string
  path: string
  content: ArtifactContent
  htmlView: HtmlView
  onHtmlViewChange: (view: HtmlView) => void
}

export type HtmlView = 'preview' | 'source'

/** 统一的图标按钮样式（34px，hover 有底 + 图标加亮）。 */
const ICON_BTN =
  'flex size-[34px] shrink-0 items-center justify-center rounded-[10px] text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground disabled:opacity-40'

/** 分段胶囊里单个标签的样式（选中态主色填充，未选态 hover 加亮文字）。 */
function segClass(active: boolean): string {
  return cn(
    'flex h-[30px] items-center gap-1.5 rounded-full px-3 text-[13px] font-medium transition-colors',
    active
      ? 'bg-primary text-primary-foreground shadow-[0_1px_3px_color-mix(in_oklab,var(--primary)_35%,transparent)]'
      : 'text-muted-foreground hover:text-foreground',
  )
}

export function ArtifactFileActions({
  threadId,
  path,
  content,
  htmlView,
  onHtmlViewChange,
}: ArtifactFileActionsProps) {
  const [downloading, setDownloading] = useState(false)
  const isHtml = content.status === 'html'

  const handleDownload = async () => {
    if (downloading) return
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

  const handleOpenNewTab = () => {
    if (content.status !== 'html') return
    window.open(content.url, '_blank', 'noopener,noreferrer')
  }

  const handleCopy = () => {
    if (content.status !== 'code' && content.status !== 'text') return
    const text = content.status === 'code' ? content.text : content.text
    void navigator.clipboard?.writeText(text)
    toast.success('已复制到剪贴板')
  }

  return (
    <div className="flex shrink-0 items-center gap-1">
      {/* 网页才需要「预览 / 源码」二选一；其它类型只有一个形态，不显示 */}
      {isHtml && (
        <div className="flex items-center gap-0.5 rounded-full border bg-muted/50 p-1">
          <button
            type="button"
            title="查看原始源码"
            onClick={() => onHtmlViewChange('source')}
            className={segClass(htmlView === 'source')}
          >
            <Code2 className="size-[15px]" />
            代码
          </button>
          <button
            type="button"
            title="预览渲染效果"
            onClick={() => onHtmlViewChange('preview')}
            className={segClass(htmlView === 'preview')}
          >
            <Eye className="size-[15px]" />
            预览
          </button>
        </div>
      )}

      {isHtml && htmlView === 'preview' && (
        <button
          type="button"
          title="在新标签页打开"
          onClick={handleOpenNewTab}
          className={ICON_BTN}
        >
          <Maximize2 className="size-4" />
        </button>
      )}

      {(content.status === 'code' || content.status === 'text') && (
        <button type="button" title="复制内容" onClick={handleCopy} className={ICON_BTN}>
          <Copy className="size-4" />
        </button>
      )}

      <button
        type="button"
        title="下载"
        disabled={downloading || content.status === 'error' || content.status === 'loading'}
        onClick={() => void handleDownload()}
        className={ICON_BTN}
      >
        {downloading ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}
      </button>
    </div>
  )
}
