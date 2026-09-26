import { FileQuestion, TriangleAlert } from '@/components/icons'

import { MarkdownContent } from '@/components/chat/MarkdownContent'
import { Skeleton } from '@/components/ui/skeleton'
import type { HtmlView } from '@/components/artifacts/ArtifactFileActions'
import { useArtifactText } from '@/core/artifacts/hooks'
import type { ArtifactContent } from '@/core/artifacts/hooks'
import { HTML_IFRAME_SANDBOX, previewUrl } from '@/core/artifacts/preview'
import { virtualPathToFilename } from '@/core/artifacts/utils'
import { formatBytes } from '@/lib/format'
import { cn } from '@/lib/utils'

/**
 * 产物正文渲染（ArtifactFileDetail）
 *
 * 职责：**只渲染正文**——按产物形态分派到 图片 / 网页(iframe) / Markdown / 代码 /
 *      文本 / 二进制 六种视图。头部与操作按钮由 ArtifactSidePanel 统一承载。
 *
 * 与早期版本的区别（本轮改造）：原先它自己 fetch 内容、自带一条头部（文件名 +
 *      预览/源码 + 新标签页 + 下载）。现在侧栏是「单行头部 + 正文」结构，
 *      头部要同时放文件选择器与操作按钮，正文再自带一条头部会白占一层高度。
 *      故把「取数」上提到 ArtifactSidePanel（用 useArtifactContent），
 *      本组件退化为纯渲染：给它 content 与 htmlView 即可。
 *
 * 安全要点（HTML 分支）：
 *   - iframe 用 blob URL + sandbox="allow-scripts allow-forms"，
 *     **刻意不给 allow-same-origin** → 文档处于不透明源，拿不到父页面的
 *     DOM / Cookie / localStorage，把「模型生成的 HTML 可能带脚本」的风险
 *     圈在 iframe 内（与 DeerFlow 一致）。
 */

interface ArtifactFileDetailProps {
  threadId: string
  path: string
  content: ArtifactContent
  htmlView: HtmlView
  className?: string
}

export function ArtifactFileDetail({
  threadId,
  path,
  content,
  htmlView,
  className,
}: ArtifactFileDetailProps) {
  const filename = virtualPathToFilename(path)

  return (
    <div className={cn('min-h-0 flex-1 overflow-hidden', className)}>
      <DetailBody content={content} filename={filename} htmlView={htmlView} threadId={threadId} path={path} />
    </div>
  )
}

interface DetailBodyProps {
  content: ArtifactContent
  filename: string
  htmlView: HtmlView
  threadId: string
  path: string
}

function DetailBody({ content, filename, htmlView, threadId, path }: DetailBodyProps) {
  if (content.status === 'idle' || content.status === 'loading') {
    return (
      <div className="space-y-2 p-4">
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </div>
    )
  }

  if (content.status === 'error') {
    return (
      <div className="flex flex-col items-center gap-3 px-6 py-12 text-center">
        <TriangleAlert className="size-8 text-amber-500" />
        <p className="text-sm font-medium">{content.message}</p>
        <p className="max-w-[15rem] text-xs leading-relaxed text-muted-foreground">
          产物可能已被移动或删除，也可能路径越界（后端已拒绝访问）。
        </p>
      </div>
    )
  }

  if (content.status === 'image') {
    return (
      <div className="flex h-full items-center justify-center overflow-auto bg-muted/20 p-4">
        <img
          src={content.url}
          alt={filename}
          className="max-h-full max-w-full rounded-lg border object-contain shadow-sm"
        />
      </div>
    )
  }

  if (content.status === 'html') {
    if (htmlView === 'source') {
      // 源码视图：走独立的 fetch 拿**未注入 <base>** 的原文
      // （useArtifactContent 的 html 分支已被改写，不能用来显示源码）
      return <HtmlSourceView threadId={threadId} path={path} />
    }
    return (
      <div className="flex h-full flex-col">
        <iframe
          title={filename}
          src={content.url}
          // 刻意不含 allow-same-origin：不透明源隔离模型生成的脚本
          sandbox={HTML_IFRAME_SANDBOX}
          className="h-full w-full flex-1 border-0 bg-white"
        />
        {/* 状态条：告知用户这是隔离渲染（新标签页打开已上移到头部操作区） */}
        <div className="flex shrink-0 items-center justify-between gap-2 border-t bg-muted/30 px-3 py-1.5">
          <span className="text-[11px] text-muted-foreground">已隔离渲染</span>
          <span className="text-[11px] text-muted-foreground">{formatBytes(content.size)}</span>
        </div>
      </div>
    )
  }

  if (content.status === 'markdown') {
    return (
      <div className="h-full overflow-y-auto px-4 py-3">
        <MarkdownContent text={content.text} />
      </div>
    )
  }

  if (content.status === 'code') {
    return (
      <div className="h-full overflow-y-auto p-3">
        <MarkdownContent text={`\`\`\`${content.lang}\n${content.text}\n\`\`\``} />
      </div>
    )
  }

  if (content.status === 'text') {
    return (
      <pre className="h-full overflow-auto p-3 font-mono text-[12px] leading-relaxed whitespace-pre-wrap break-words">
        {content.text}
      </pre>
    )
  }

  // binary
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-12 text-center">
      <FileQuestion className="size-8 text-muted-foreground" />
      <p className="text-sm text-muted-foreground">该文件类型暂不支持内联预览</p>
      <p className="text-xs text-muted-foreground/70">{formatBytes(content.size)} · 请下载后查看</p>
    </div>
  )
}

/**
 * HTML 源码视图。
 *
 * 为什么不复用 useArtifactContent 的 text：那个 hook 对 html 走的是 blob 分支
 * 且已注入 <base>，拿不到「原始源码」。这里用 useArtifactText 单独 fetch 一次
 * 拿原文，保证源码视图所见即文件内容。开销可接受（用户主动切到源码态时才发请求）。
 */
function HtmlSourceView({ threadId, path }: { threadId: string; path: string }) {
  const source = useArtifactText({ threadId, path, enabled: true })

  if (source.status === 'idle' || source.status === 'loading') {
    return (
      <div className="space-y-2 p-4">
        <Skeleton className="h-4 w-2/5" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-4 w-3/5" />
      </div>
    )
  }

  if (source.status === 'error') {
    return (
      <div className="flex flex-col items-center gap-3 px-6 py-12 text-center">
        <TriangleAlert className="size-8 text-amber-500" />
        <p className="text-sm font-medium">{source.message}</p>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <pre className="min-h-0 flex-1 overflow-auto p-3 font-mono text-[12px] leading-relaxed whitespace-pre">
        {source.text}
      </pre>
      <div className="flex shrink-0 items-center justify-between border-t bg-muted/30 px-3 py-1.5 text-[11px] text-muted-foreground">
        <span>原始源码（未注入 &lt;base&gt;）</span>
        <span>{formatBytes(source.size)}</span>
      </div>
    </div>
  )
}

/** 供父组件复用的预览 URL（导出便于测试）。 */
export { previewUrl }
