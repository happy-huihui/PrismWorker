import type { ReactNode } from 'react'
import { Sparkles } from 'lucide-react'

import { cn } from '@/lib/utils'

interface MessageBubbleProps {
  role: 'user' | 'assistant'
  /** 是否为「进行中」的流式消息（阶段 5 增加光标动画） */
  streaming?: boolean
  children: ReactNode
}

export function MessageBubble({ role, children, streaming }: MessageBubbleProps) {
  const isUser = role === 'user'

  return (
    <div
      className={cn(
        'flex w-full gap-2.5 animate-message-in',
        isUser ? 'justify-end' : 'justify-start',
      )}
    >
      {!isUser && (
        <div
          aria-hidden
          className="mt-1 flex size-7 shrink-0 items-center justify-center rounded-full border bg-gradient-to-br from-accent to-background text-foreground/70 shadow-sm"
        >
          <Sparkles className="size-3.5" />
        </div>
      )}

      <div
        className={cn(
          'max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed break-words',
          isUser
            ? 'rounded-br-md bg-primary text-primary-foreground shadow-sm'
            : 'rounded-tl-md border bg-card text-card-foreground shadow-sm',
        )}
      >
        {children}
        {streaming && !isUser && (
          <span aria-hidden className="ml-0.5 inline-block h-3.5 w-[2px] translate-y-[2px] animate-pulse rounded-full bg-foreground/70" />
        )}
      </div>
    </div>
  )
}