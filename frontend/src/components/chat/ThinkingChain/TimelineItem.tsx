import { useEffect, useRef, useState } from 'react'
import { ChevronDown } from '@/components/icons'

import { cn } from '@/lib/utils'

import { type ChainStep } from '@/core/runs/useRunStream'

import { MarkdownContent } from '../MarkdownContent'

import { ReasoningBlock } from './ReasoningBlock'
import { ToolRow } from './ToolRow'

/**
 * 时间线一行（TimelineItem）
 *
 * 职责：渲染一个 ChainStep——左侧轨道（节点 + 连接线），右侧内容
 *      （模型思考 / 本轮叙述 / 工具行）。对齐 DeerFlow 的 `ChainOfThoughtStep`：
 *        - reasoning 段：与叙述同为正文内容，用 Markdown 渲染（支持列表/加粗等），
 *          但整体降一级（次级色、字号略小），不抢最终答复的戏；
 *        - narration 段：模型「边说边做」的叙述，正文色，也用 Markdown 渲染；
 *        - tool 段：一次工具调用，走 ToolRow（语义图标 + 标题 + 目标 chip）。
 * 轨道节点：工具行 = 7px 主色点 + 柔光圈（完成静态 / 进行中共振呼吸 / 失败红）；
 *          文本行 = 5px 中性小点，安静不抢戏。
 *
 * 为什么用 Markdown：DeerFlow 的思考链文本段就是 MarkdownContent 渲染的
 * （见 message-group.tsx 的 renderStep→renderAssistantText / reasoning 分支），
 * 模型思考里常有编号列表、加粗小节，纯 <p> 会退化成一大段。
 */
interface TimelineItemProps {
  step: ChainStep
  isLast: boolean
  active: boolean
}

/** 叙述折叠阈值：超过就默认收起（模型偶尔会把整段规划写进叙述，不折叠会刷屏） */
const NARRATION_FOLD_CHARS = 160

/**
 * 叙述文本（NarrationText）——超长默认折叠。
 *
 * 为什么叙述也要折叠（2026-09-26）：实测模型会把「设计思考：1. 目的… 2. 受众…」
 * 这类整段规划写进叙述位（而不是思考里），一条就占掉思考链半屏。
 * 策略：流式进行中恒展开（实时看着写）；流式结束且超长 → 自动收起为首段 +
 * 「展开」按钮（用户点开后尊重用户意图，不再自动收）。
 */
function NarrationText({ text, streaming }: { text: string; streaming: boolean }) {
  const tooLong = text.length > NARRATION_FOLD_CHARS
  const [open, setOpen] = useState(streaming || !tooLong)
  const userTouchedRef = useRef(false)

  useEffect(() => {
    // 流式结束：长叙述自动收起；用户已手动操作过则尊重其选择
    if (!streaming && !userTouchedRef.current) setOpen(!tooLong)
  }, [streaming, tooLong])

  if (!tooLong) {
    return (
      <MarkdownContent
        text={text}
        streaming={streaming}
        className="text-[13px] leading-[1.7] text-foreground/90"
      />
    )
  }

  const preview = open ? text : `${text.slice(0, NARRATION_FOLD_CHARS).trimEnd()}…`

  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={() => {
          userTouchedRef.current = true
          setOpen((v) => !v)
        }}
        aria-expanded={open}
        title={open ? '收起叙述' : '展开完整叙述'}
        className="-ml-1.5 mb-0.5 flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
      >
        <ChevronDown
          className={cn('size-3 shrink-0 transition-transform', open && 'rotate-180')}
        />
        <span>说明 · {text.length} 字</span>
      </button>
      <MarkdownContent
        text={preview}
        streaming={streaming}
        className="text-[13px] leading-[1.7] text-foreground/90"
      />
    </div>
  )
}

export function TimelineItem({ step, isLast, active }: TimelineItemProps) {
  const isTool = step.kind === 'tool'
  const running = isTool && step.item.status === 'running'
  const failed = isTool && step.item.status === 'failed'

  // 节点配色：完成=主色、进行中=主色+呼吸、失败=红、思考=空心、叙述=中性
  const dotClass = !isTool
    ? step.kind === 'reasoning'
      ? 'size-[5px] border border-muted-foreground/40 bg-transparent'
      : 'size-[5px] bg-muted-foreground/50'
    : failed
      ? 'size-[7px] bg-destructive shadow-[0_0_0_3px_color-mix(in_oklab,var(--destructive)_14%,transparent)]'
      : running
        ? 'size-[7px] bg-primary shadow-[0_0_0_3px_color-mix(in_oklab,var(--primary)_18%,transparent)]'
        : 'size-[7px] bg-primary/70 shadow-[0_0_0_3px_color-mix(in_oklab,var(--primary)_10%,transparent)]'

  return (
    <div className="animate-step-in flex gap-3">
      {/* 1.左侧轨道：小圆点节点（与首行文字光居对齐）+ 向下连接线 */}
      <div className="relative mt-[9px] flex w-5 shrink-0 flex-col items-center">
        <span className={cn('relative shrink-0 rounded-full', dotClass)}>
          {running && (
            <span
              aria-hidden
              className="absolute inset-0 animate-ping rounded-full bg-primary/40"
            />
          )}
        </span>
        {/* 末尾且仍在进行时不画连接线 */}
        {!(isLast && active) && <span aria-hidden className="mt-[3px] w-[2px] flex-1 rounded-full bg-border" />}
      </div>

      {/* 2.右侧内容 */}
      <div className="min-w-0 flex-1 pb-3.5">
        {isTool ? (
          <ToolRow item={step.item} />
        ) : step.kind === 'reasoning' ? (
          // 思考：默认折叠成一行摘要（原始 CoT 动辄上万字，见 ReasoningBlock 注释）
          <ReasoningBlock text={step.text} streaming={active} />
        ) : (
          // 叙述：模型「边说边做」的可见正文，超长自动折叠（见 NarrationText 注释）
          <NarrationText text={step.text} streaming={active && isLast} />
        )}
      </div>
    </div>
  )
}
