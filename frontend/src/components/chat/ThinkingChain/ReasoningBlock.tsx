import { useEffect, useRef, useState } from 'react'
import { ChevronDown } from '@/components/icons'

import { cn } from '@/lib/utils'

import { MarkdownContent } from '../MarkdownContent'

/**
 * 思考块（ReasoningBlock）
 *
 * 职责：渲染一段模型的 `reasoning_content`（隐藏思维链），超长自动折叠。
 *
 * 为什么折叠（实测依据）：
 *      2026-09-23 实测一次普通「做个网站」的 run，reasoning 累计 **20,319 字符**
 *      （而模型可见正文只有 7,813 字符）。若全文铺开，思考链会被一大段
 *      英文思维链淹没，用户根本看不到真正有用的「工具步骤 + 叙述」。
 *      DeerFlow 之所以显得简洁，是因为它只展示 narration + 工具步骤、不展示原始 CoT。
 *      本项目的取舍：超长自动折叠（视图对齐 DeerFlow 的简洁），但**保留全文可展开**——
 *      排查「模型为什么这么决策」时，这份思维链恰恰是最有价值的信息，直接丢弃可惜。
 *
 * 折叠时机（2026-09-26 用户反馈）：此前只在「挂载时已超长」才默认收起——
 *      流式中从短变长的思考段会一直铺开（截图实锤：思考 · 4153 字全程展开）。
 *      现改为**越过阈值即自动收起（含流式中途）**：折叠态的「思考 · N 字」
 *      实时增长，既保持安静，又让用户知道模型确实在思考（而不是卡住了）；
 *      用户手动展开过则尊重其选择，不再自动收。
 */

interface ReasoningBlockProps {
  text: string
  /** 该步是否仍在流式输出（透传给 MarkdownContent 的 streaming） */
  streaming: boolean
}

/** 字数摘要：2 万字的思维链不该默认铺满屏幕 */
function formatChars(n: number): number | string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万字`
  if (n >= 1000) return `${(n / 1000).toFixed(1)} 千字`
  return `${n}`
}

/** 思考折叠阈值：超过此字数自动收起为首段预览（调这里改行为） */
const REASONING_FOLD_CHARS = 400

/**
 * 折叠策略：
 *   - 短文（≤ REASONING_FOLD_CHARS）：直接全展开，不显示折叠按钮（短文没有折叠必要）
 *   - 长文：流式中一旦越过阈值立即收起为首 400 字预览 + 「展开 N 字全文」按钮；
 *     用户点开过就不再自动收（尊重用户意图）。
 */
export function ReasoningBlock({ text, streaming }: ReasoningBlockProps) {
  const tooLong = text.length > REASONING_FOLD_CHARS
  const [open, setOpen] = useState(!tooLong)
  const userTouchedRef = useRef(false)

  useEffect(() => {
    // 越过阈值自动收起（含流式中途）；流式结束时仍超长也收起。
    // 用户手动展开/收起过后不再干预。
    if (userTouchedRef.current) return
    if (tooLong || !streaming) setOpen(!tooLong)
  }, [tooLong, streaming])

  const preview = tooLong && !open ? text.slice(0, REASONING_FOLD_CHARS) : text
  const collapsedTailLen =
    tooLong && !open ? Math.max(text.length - REASONING_FOLD_CHARS, 0) : 0

  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={() => {
          userTouchedRef.current = true
          setOpen((v) => !v)
        }}
        aria-expanded={open}
        title={open ? '收起思考' : '展开完整思考'}
        // -ml-1.5 抵消 px-1.5：让 chevron 与上方/下方文本段的左边缘对齐
        className="-ml-1.5 flex max-w-full items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[12px] text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
      >
        <ChevronDown
          className={cn('size-3 shrink-0 transition-transform', open && 'rotate-180')}
        />
        <span>思考</span>
        <span className="text-muted-foreground/70">· {text.length >= 10000 ? formatChars(text.length) : `${text.length} 字`}</span>
      </button>

      {/* 折叠态截断展示：首 400 字 + 「▼ 展开全部 N 字」提示 */}
      {preview && (
        <MarkdownContent
          text={preview}
          streaming={streaming}
          className="mt-1.5 text-[13px] leading-[1.7] text-muted-foreground"
        />
      )}
      {!open && collapsedTailLen > 0 && (
        <button
          type="button"
          onClick={() => {
            userTouchedRef.current = true
            setOpen(true)
          }}
          className="mt-1 text-[11px] text-muted-foreground/70 hover:text-foreground"
        >
          ▼ 展开后 {collapsedTailLen >= 10000 ? `${(collapsedTailLen / 10000).toFixed(1)} 万字` : `${collapsedTailLen} 字`} 全文
        </button>
      )}
    </div>
  )
}
