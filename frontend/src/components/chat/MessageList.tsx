import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, MessagesSquare } from 'lucide-react'

import { Skeleton } from '@/components/ui/skeleton'
import { type MessageOut } from '@/core/api/types'
import {
  type RunStreamStatus,
  type ToolCallItem,
} from '@/core/runs/useRunStream'
import { cn } from '@/lib/utils'

import { MarkdownContent } from './MarkdownContent'
import { MessageBubble } from './MessageBubble'
import { RunStatusBar } from './RunStatusBar'
import { ThinkingPanel } from './ThinkingPanel'
import { ArtifactCardList } from '@/components/artifacts/ArtifactCardList'

export interface RunActivity {
  prints: string[]
  toolCalls: ToolCallItem[]
  /** 模型思考叙述累积（thinking_chunk → 思考链文本行） */
  thinkingText?: string
  active: boolean
  /** run 开始时间（秒时间戳，ThinkingPanel 流式计时用） */
  startedAt?: number | null
  /** run 结束时间（秒时间戳，ThinkingPanel 耗时展示用） */
  finishedAt?: number | null
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

  // 最后一条用户消息的索引：思考链面板应插在「用户问题之后、本轮回复之前」，
  // 而不是在完整回复的后面（结束后 history 已含回复，直接平铺会把思考链挤到下边）
  const lastUserIdx = useMemo(() => {
    let idx = -1
    for (let i = 0; i < history.length; i++) {
      if (history[i].role === 'user') idx = i
    }
    return idx
  }, [history])
  const headHistory = lastUserIdx >= 0 ? history.slice(0, lastUserIdx + 1) : history
  const tailHistory = lastUserIdx >= 0 ? history.slice(lastUserIdx + 1) : []

  const hasActivity = !!activity && (activity.prints.length > 0 || activity.toolCalls.length > 0)
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
      <div className="flex flex-col gap-3 px-4 py-6">
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

  return (
    <div className="relative flex w-full flex-col gap-2 px-4 py-4 sm:px-6">
      {headHistory.map((m, i) => {
        const isUser = m.role === 'user'
        return (
          <MessageBubble key={i} role={isUser ? 'user' : 'assistant'}>
            {isUser ? (
              <span className="whitespace-pre-wrap break-words">{m.content}</span>
            ) : (
              <MarkdownContent text={m.content} />
            )}
          </MessageBubble>
        )
      })}

      {hasActivity && activity && (
        <ThinkingPanel
          prints={activity.prints}
          toolCalls={activity.toolCalls}
          thinkingText={activity.thinkingText}
          active={activity.active}
          startedAt={activity.startedAt}
          finishedAt={activity.finishedAt}
        />
      )}

      {tailHistory.map((m, i) => {
        const isUser = m.role === 'user'
        return (
          <MessageBubble key={`tail-${i}`} role={isUser ? 'user' : 'assistant'}>
            {isUser ? (
              <span className="whitespace-pre-wrap break-words">{m.content}</span>
            ) : (
              <MarkdownContent text={m.content} />
            )}
          </MessageBubble>
        )
      })}

      {streamTail && (
        <MessageBubble role="assistant" streaming>
          <MarkdownContent text={stripFinishMarker(streamTail.content)} streaming />
        </MessageBubble>
      )}

      {threadId && artifacts && artifacts.length > 0 && (
        <ArtifactCardList threadId={threadId} paths={artifacts} onPreview={onArtifactPreview ?? (() => {})} />
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
  )
}