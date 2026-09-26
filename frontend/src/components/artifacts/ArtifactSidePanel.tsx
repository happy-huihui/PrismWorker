import { useEffect, useState } from 'react'
import { PanelRightClose } from '@/components/icons'

import { ArtifactFileActions, type HtmlView } from '@/components/artifacts/ArtifactFileActions'
import { ArtifactFileDetail } from '@/components/artifacts/ArtifactFileDetail'
import { ArtifactSwitcher } from '@/components/artifacts/ArtifactSwitcher'
import { useArtifacts } from '@/core/artifacts/context'
import { useArtifactContent } from '@/core/artifacts/hooks'
import { PANEL_WIDTH_MAX, PANEL_WIDTH_MIN, usePanelResize } from '@/core/artifacts/usePanelResize'
import { cn } from '@/lib/utils'

/**
 * 产物侧边栏（ArtifactSidePanel）
 *
 * 职责：右侧常驻面板，承担「直接展示当前产物 + 切换产物」两件事。
 *
 * 本轮改造（对齐 DeerFlow 的产物区体验，但按本项目裁剪）：
 *   1. **去掉独立清单列**。原先是「清单列 + 详情列」两栏，清单长期占掉面板
 *      约 1/3 宽度，而多数会话只有 1~2 个产物 —— 这份宽度让给预览本身更划算。
 *      切换入口改到头部：ArtifactSwitcher（单产物时退化为静态标签）。
 *   2. **可拖拽调宽**。面板左缘是可拖手柄（见 usePanelResize），双击复位，
 *      方向键微调，宽度按工作区持久化。
 *   3. **单行头部**。文件选择器 + 操作按钮 + 收起共处一行，正文不再自带头部。
 *
 * 布局：aside 自身是 flex 列 —— 头部固定高，正文 flex-1 撑满。
 *      宽度由 hook 以内联 style 给出（动态值无法用 Tailwind 类表达）。
 *
 * 开关状态与选中项来自 ArtifactsContext（会话级持久化，见 core/artifacts/context.tsx）。
 */

interface ArtifactSidePanelProps {
  threadId: string
  className?: string
}

export function ArtifactSidePanel({ threadId, className }: ArtifactSidePanelProps) {
  const { artifacts, selectedPath, select, setOpen } = useArtifacts()
  const { width, dragging, onHandlePointerDown, nudgeWidth, resetWidth } = usePanelResize()

  /** 网页产物的「预览 / 源码」视图；其它类型忽略此值 */
  const [htmlView, setHtmlView] = useState<HtmlView>('preview')

  const hasArtifacts = artifacts.length > 0
  // 有产物但用户还没点过：默认展示第一个，避免右侧大片空白
  const activePath = selectedPath ?? (hasArtifacts ? artifacts[0] : null)

  // 换文件时回到「预览」：否则从 A 的源码态切到 B 会停在源码态，很突兀
  useEffect(() => {
    setHtmlView('preview')
  }, [activePath])

  // 侧栏打开即加载内容（关闭时组件不渲染，故 enabled 恒为 true）
  const content = useArtifactContent({ threadId, path: activePath, enabled: activePath != null })

  const onHandleKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    // 方向键微调；面板在右侧，左方向键 = 变宽
    const STEP = 16
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      nudgeWidth(STEP)
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      nudgeWidth(-STEP)
    } else if (event.key === 'Home') {
      event.preventDefault()
      resetWidth()
    }
  }

  return (
    <aside
      style={{ width }}
      className={cn(
        'relative flex h-full min-h-0 shrink-0 flex-col border-l bg-background',
        className,
      )}
      aria-label="产物预览"
    >
      {/* 拖拽手柄：覆盖在左边缘上，视觉上就是把 border-l 点亮 */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="调整产物栏宽度"
        aria-valuenow={width}
        aria-valuemin={PANEL_WIDTH_MIN}
        aria-valuemax={PANEL_WIDTH_MAX}
        tabIndex={0}
        title="拖动调整宽度 · 双击复位"
        onPointerDown={onHandlePointerDown}
        onDoubleClick={resetWidth}
        onKeyDown={onHandleKeyDown}
        className="group absolute inset-y-0 left-0 z-10 flex w-2 cursor-col-resize justify-start outline-none"
      >
        <span
          className={cn(
            'h-full w-0.5 rounded-full transition-colors',
            dragging ? 'bg-primary' : 'bg-transparent group-hover:bg-primary/50',
            'group-focus-visible:bg-primary',
          )}
        />
      </div>

      {/* 单行头部：当前文件（可切换）+ 操作 + 收起。
          视觉规格（2026-09-25 用户定稿「方案 A」）：
            顶栏 60px（h-[60px]），三段式 —— 文件 chip 居左、操作组 + 细分隔线 + 收起居右。
            之前 h-14(56px) 是 2026-09-23 放大过的；本次再提到 60px 并按 DeerFlow
            布局重排，文件名 15px、按钮 34px、分段带文字标签。 */}
      <div className="flex h-[60px] shrink-0 items-center gap-1.5 border-b bg-card pr-2.5 pl-3.5">
        <ArtifactSwitcher
          paths={artifacts}
          current={activePath}
          onSelect={select}
          className="min-w-0 flex-1"
        />

        {activePath && (
          <ArtifactFileActions
            threadId={threadId}
            path={activePath}
            content={content}
            htmlView={htmlView}
            onHtmlViewChange={setHtmlView}
          />
        )}

        {/* 细分隔线：把「收起」与前面的操作组在视觉上隔开（DeerFlow 布局） */}
        <div aria-hidden className="mx-1 h-5 w-px shrink-0 bg-border" />

        <button
          type="button"
          onClick={() => setOpen(false)}
          title="收起产物栏"
          className="flex size-[34px] shrink-0 items-center justify-center rounded-[10px] text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground"
        >
          <PanelRightClose className="size-[17px]" />
        </button>
      </div>

      {/* 正文：直接展示当前产物 */}
      {activePath ? (
        <ArtifactFileDetail
          threadId={threadId}
          path={activePath}
          content={content}
          htmlView={htmlView}
        />
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
          <p className="text-xs text-muted-foreground">本轮暂无产物</p>
          <p className="text-[11px] leading-relaxed text-muted-foreground/70">
            当模型生成图片、网页或文档并交付时，会出现在这里。
          </p>
        </div>
      )}
    </aside>
  )
}
