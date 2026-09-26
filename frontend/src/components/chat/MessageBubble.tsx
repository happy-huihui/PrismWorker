import type { ReactNode } from 'react'

/**
 * 消息气泡（MessageBubble）
 *
 * 对齐模板：user = 右对齐软卡片（panel 底细边、右下 4px 小圆角、15px 字）；
 *          assistant = 无气泡无头像的纯文本流（15px / 1.7 行高，像文档正文）。
 */
interface MessageBubbleProps {
  role: 'user' | 'assistant'
  /** 是否为「进行中」的流式消息（末尾闪烁光标） */
  streaming?: boolean
  /** 会话轮次序号（仅 user 消息传入）：挂到 DOM 上供轮次快速索引定位/滚动 */
  turnIndex?: number
  children: ReactNode
}

export function MessageBubble({ role, children, streaming, turnIndex }: MessageBubbleProps) {
  const isUser = role === 'user'

  if (isUser) {
    return (
      <div data-turn={turnIndex} className="flex w-full animate-message-in justify-end">
        <div className="max-w-[80%] rounded-[16px] rounded-br-[4px] border bg-card px-4 py-3 text-[15px] leading-relaxed break-words whitespace-pre-wrap shadow-sm">
          {children}
        </div>
      </div>
    )
  }

  return (
    <div className="flex w-full animate-message-in justify-start">
      <div className="min-w-0 flex-1 text-[15px] leading-[1.7] break-words">
        {children}
        {streaming && (
          <span aria-hidden className="ml-0.5 inline-block h-3.5 w-[2px] translate-y-[2px] animate-pulse rounded-full bg-foreground/70" />
        )}
      </div>
    </div>
  )
}
