import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, MessagesSquare } from '@/components/icons'

import { Skeleton } from '@/components/ui/skeleton'
import { type MessageOut } from '@/core/api/types'
import {
  type ChainStep,
  type RoutingInfo,
  type RunStreamState,
  type RunStreamStatus,
  type ToolCallItem,
} from '@/core/runs/useRunStream'
import { cn } from '@/lib/utils'

import { ClarificationBubble } from './ClarificationBubble'
import { MarkdownContent } from './MarkdownContent'
import { MessageBubble } from './MessageBubble'
import { RunStatusBar } from './RunStatusBar'
import { ThinkingChain } from './ThinkingChain/ThinkingChain'
import { ArtifactCardList } from '@/components/artifacts/ArtifactCardList'

export interface RunActivity {
  /** 本轮思考链步骤（模型思考 / 叙述 / 工具调用，按到达顺序） */
  steps: ChainStep[]
  active: boolean
  /** run 开始时间（秒时间戳，ThinkingChain 流式计时用） */
  startedAt?: number | null
  /** run 结束时间（秒时间戳，ThinkingChain 耗时展示用） */
  finishedAt?: number | null
  /** 思考开关被降级（模型不支持） */
  degraded?: boolean
  /** 后端动态路由的决策（模型名 + 来源 + 理由） */
  routing?: RoutingInfo | null
}
export interface RunStatusView {
  status: RunStreamStatus
  messageCount: number | null
  error: string | null
  startedAt: number | null
  finishedAt: number | null
}

interface MessageListProps {
  messages: MessageOut[]
  isLoading?: boolean
  /** 消息流底部是否存在「进行中」的流式回复 */
  streaming?: boolean
  /** 本轮 run 的活动数据（思考链 + 工具调用），非空即渲染在流式回复之前 */
  activity?: RunActivity | null
  /** run 终态数据（finished/error/cancelled 时渲染终态条） */
  runStatus?: RunStatusView | null
  /** 产物虚拟路径列表（artifacts 事件 + 近期 run 兜底） */
  artifacts?: string[]
  /** 点击产物卡片「预览」 */
  onArtifactPreview?: (path: string) => void
  /** 产物所属线程（构造下载/预览 URL） */
  threadId?: string
  /** 历史思考链快照（按 run 创建时间正序）：逐个附到对应的 assistant 回复之前回放 */
  chains?: RunStreamState[]
  /** 澄清气泡的选项被点击时回调：把选项文本塞进下方输入框（可选） */
  onFillInput?: (text: string) => void
  /** 澄清气泡的选项被点击时直接发送为回复（与 onFillInput 互斥；优先于它） */
  onSendOption?: (text: string) => void
}

/** 判断一个 tool call 名称是否是 ask_clarification（兼容带命名空间的形式）。 */
function isAskClarification(toolName: string): boolean {
  return toolName === 'ask_clarification' || toolName.endsWith('ask_clarification')
}

/** 从思考链步骤里挑出最近一个完成态的 ask_clarification 工具调用（用于渲染气泡）。 */
function pickClarificationTool(steps: ChainStep[]): ToolCallItem | null {
  let picked: ToolCallItem | null = null
  for (const s of steps) {
    if (s.kind !== 'tool') continue
    if (!isAskClarification(s.item.tool)) continue
    // 优先取完成态；进行中也展示（避免漏掉运行中的问句）
    if (s.item.status === 'running' || s.item.status === 'completed' || s.item.status === 'failed') {
      picked = s.item
    }
  }
  return picked
}

function isTool(role: string) {
  return role === 'tool'
}

/** 后端防注入中间件的 user 输入边界标记（兼容历史遗留数据，渲染前剥离） */
const USER_INPUT_BEGIN = '--- BEGIN USER INPUT ---'
const USER_INPUT_END = '--- END USER INPUT ---'

/** neutralize 后遗留的惰性标记行 */
const NEUTRALIZED_BOUNDARY_LINE_RE = /^\s*\[(?:BEGIN|END) USER INPUT\]\s*$/gm

/** 模型回答末尾的 <finish/> 完成标记（内部信号，不展示给用户） */
const FINISH_MARKER_RE = /\s*<\/?finish\/?>\s*$/i

function stripFinishMarker(text: string): string {
  return text.replace(FINISH_MARKER_RE, '').trim()
}

/** 去除 user 消息的会话包裹标记（真实 BEGIN/END + 惰性标记残留），还原纯文本 */
function stripUserInputWrapper(text: string): string {
  let cur = text
  for (let i = 0; i < 16; i++) {
    const b = cur.indexOf(USER_INPUT_BEGIN)
    const e = cur.lastIndexOf(USER_INPUT_END)
    if (b < 0 || e <= b) break
    cur = cur.slice(b + USER_INPUT_BEGIN.length, e)
  }
  return cur
    .replace(NEUTRALIZED_BOUNDARY_LINE_RE, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/**
 * 历史消息白名单过滤：
 * - 只展示 user / assistant 两类消息（system 系统提示词、function/message 角色、
 *   未知角色一律视为内部信息，不进入对话流）
 * - 丢弃空内容消息
 * - 相邻同角色同内容的重复消息（重复发送 / 流式重复落盘）只保留一条
 */
function renderableHistory(messages: MessageOut[]): MessageOut[] {
  const out: MessageOut[] = []
  let lastKey: string | null = null
  for (const m of messages) {
    if (m.role !== 'user' && m.role !== 'assistant') continue
    if (isTool(m.role)) continue
    const raw = (m.content ?? '').trim()
    if (!raw) continue
    const content =
      m.role === 'user' ? stripUserInputWrapper(raw) : stripFinishMarker(raw)
    if (!content) continue
    const key = `${m.role}\u0000${content}`
    if (key === lastKey) continue
    lastKey = key
    out.push({ role: m.role, content })
  }
  return out
}

export function MessageList({
  messages,
  isLoading,
  streaming,
  activity,
  runStatus,
  artifacts,
  onArtifactPreview,
  threadId,
  chains,
  onFillInput,
  onSendOption,
}: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' })
  }, [messages])

  useEffect(() => {
    const t = window.setTimeout(() => {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }, 120)
    return () => window.clearTimeout(t)
  }, [messages])

  const last = messages.length > 0 ? messages[messages.length - 1] : null
  const streamTail = streaming && last != null && last.role === 'assistant' ? last : null
  const rest = streamTail ? messages.slice(0, -1) : messages
  const history = useMemo(() => renderableHistory(rest), [rest])

  // 按 user 消息切「回合」：第 i 回合 = 第 i 条 user 消息 + 其后直到下一 user 的
  // assistant 回复。思考链按回合配对（一次提交 = 一个 run = 一条链，chains 按
  // run 创建时间正序，与回合一一对应）。
  //
  // 为什么不能按 assistant 消息配对（旧逻辑 chainIdx 只在 assistant 上自增）：
  // 澄清 run（ask_clarification return_direct）不产生 assistant 正文消息，
  // 它的链在历史里无处挂载——要么消失（澄清卡片丢失），要么被下一条
  // assistant 错消费（run1 的链挂到 run2 的回复前，整体错位一位）。
  const turns = useMemo(() => {
    const list: { user: MessageOut | null; replies: MessageOut[] }[] = []
    for (const m of history) {
      if (m.role === 'user') {
        list.push({ user: m, replies: [] })
      } else if (list.length === 0) {
        list.push({ user: null, replies: [m] })
      } else {
        list[list.length - 1].replies.push(m)
      }
    }
    return list
  }, [history])
  // 本轮活动（实时思考链）插在最后一条 user 消息之后、其回复之前：
  // 即最后一个含 user 的回合内部。没有 user 回合时退化为插到所有回合之后。
  const lastUserTurnIdx = useMemo(() => {
    for (let i = turns.length - 1; i >= 0; i--) {
      if (turns[i].user) return i
    }
    return -1
  }, [turns])

  // 有步骤、或本轮正在进行（要出「正在处理…」占位）时，思考链卡片都要出现
  const hasActivity =
    !!activity && (activity.steps.length > 0 || activity.active)
  const finalStatus = runStatus

  const [showFab, setShowFab] = useState(false)
  useEffect(() => {
    const el = bottomRef.current
    if (!el || messages.length === 0) {
      setShowFab(false)
      return
    }
    const io = new IntersectionObserver(
      ([entry]) => setShowFab(!entry.isIntersecting),
      { rootMargin: '0px 0px -96px 0px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [messages.length])

  if (isLoading) {
    return (
      <div className="mx-auto flex w-full max-w-[820px] flex-col gap-3 px-4 py-6">
        <Skeleton className="ml-auto h-16 w-2/3" />
        <Skeleton className="h-20 w-3/4" />
        <Skeleton className="ml-auto h-16 w-1/2" />
      </div>
    )
  }

  if (messages.length === 0 && !hasActivity) {
    return (
      <div className="relative flex h-full flex-col items-center justify-center gap-3 text-center">
        <div className="flex size-12 items-center justify-center rounded-2xl bg-muted text-muted-foreground">
          <MessagesSquare className="size-6" />
        </div>
        <p className="text-sm font-medium text-muted-foreground">还没有消息</p>
        <p className="max-w-xs text-xs text-muted-foreground/80">
          在下方向 PrismWorker 发送第一条消息，开始你的探索。
        </p>
      </div>
    )
  }

  // 会话轮次计数：供 TurnIndex 定位（每次渲染重置）
  let userTurn = -1
  // 历史思考链索引：逐个「含 user 的回合」消费一条（回合序 = run 创建序）
  let chainIdx = 0

  // 活动块（实时思考链 + 澄清气泡）渲染片段，复用于「回合内」与「无回合」两种位置
  const activityBlock = hasActivity && activity && (
    <>
      <ThinkingChain
        steps={activity.steps}
        active={activity.active}
        startedAt={activity.startedAt}
        finishedAt={activity.finishedAt}
        degraded={activity.degraded}
        pending={activity.active && activity.steps.length === 0}
        routing={activity.routing}
      />
      {pickClarificationTool(activity.steps) && (
        <ClarificationBubble
          tool={pickClarificationTool(activity.steps)!}
          onFillInput={onFillInput}
          onSendOption={onSendOption}
        />
      )}
    </>
  )

  return (
    // 模板 .body/.wrap：上 26px、左右 28px、下 8px；内容 820px 居中、行间距 22px
    <div className="relative flex w-full flex-col px-7 pt-[26px] pb-2">
      <div className="mx-auto flex w-full max-w-[820px] flex-col gap-[22px]">
      {turns.map((turn, ti) => {
        // 无 user 的领头回合（异常历史，如旧压缩数据）不消费链，保持后续配对不错位
        const chain = turn.user ? (chains?.[chainIdx++] ?? null) : null
        return (
          <Fragment key={ti}>
            {turn.user && (
              <MessageBubble role="user" turnIndex={++userTurn}>
                <span className="whitespace-pre-wrap break-words">{turn.user.content}</span>
              </MessageBubble>
            )}
            {chain && (
              <>
                <ThinkingChain
                  steps={chain.steps}
                  active={false}
                  startedAt={chain.startedAt}
                  finishedAt={chain.finishedAt}
                />
                {pickClarificationTool(chain.steps) && (
                  <ClarificationBubble
                    tool={pickClarificationTool(chain.steps)!}
                    onFillInput={onFillInput}
                    onSendOption={onSendOption}
                  />
                )}
              </>
            )}
            {ti === lastUserTurnIdx && activityBlock}
            {turn.replies.map((m, ri) => (
              <MessageBubble key={ri} role="assistant">
                <MarkdownContent text={m.content} />
              </MessageBubble>
            ))}
          </Fragment>
        )
      })}

      {/* 没有含 user 的回合时（首轮刚提交历史未回写 / 异常历史），活动块落在最后 */}
      {lastUserTurnIdx < 0 && activityBlock}

      {streamTail && (
        <MessageBubble role="assistant" streaming>
          <MarkdownContent text={stripFinishMarker(streamTail.content)} streaming />
        </MessageBubble>
      )}

  {threadId && artifacts && artifacts.length > 0 && (
    <ArtifactCardList threadId={threadId} paths={artifacts} onPreview={onArtifactPreview} />
  )}

      {finalStatus && (
        <RunStatusBar
          status={finalStatus.status}
          messageCount={finalStatus.messageCount}
          error={finalStatus.error}
          startedAt={finalStatus.startedAt}
          finishedAt={finalStatus.finishedAt}
        />
      )}

      <div ref={bottomRef} aria-hidden className={cn(!streamTail && !hasActivity && 'h-px')} />

      {showFab && (
        <button
          type="button"
          onClick={() => {
            setShowFab(false)
            bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
          }}
          className="absolute right-5 bottom-5 z-10 flex size-9 items-center justify-center rounded-full border bg-background shadow-lg transition-colors hover:bg-muted"
          title="回到底部"
        >
          <ChevronDown className="size-4" />
        </button>
      )}
      </div>
    </div>
  )
}