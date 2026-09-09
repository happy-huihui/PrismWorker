import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface MessageBubbleProps {
  role: 'user' | 'assistant' | 'system'
  /** 是否为「进行中」的流式消息（阶段 5 增加光标动画） */
  streaming?: boolean
  children: ReactNode
}

export function MessageBubble({ role, children, streaming }: MessageBubbleProps) {
  if (role === 'system') {
    return (
      <div className="flex justify-center py-1">
        <span className="rounded-full bg-muted/60 px-3 py-1 text-xs text-muted-foreground">
          {children}
        </span>
      </div>
    )
  }

  const isUser = role === 'user'

  return (
    <div className={cn('flex w-full', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[85%] whitespace-pre-wrap rounded-xl px-3.5 py-2.5 text-sm leading-relaxed break-words',
          isUser
            ? 'bg-primary text-primary-foreground'
            : 'border bg-card text-card-foreground',
          streaming &&
            !isUser &&
            'after:ml-0.5 after:inline-block after:h-3.5 after:w-0.5 after:animate-pulse after:bg-foreground after:content-[""]',
        )}
      >
        {children}
      </div>
    </div>
  )
}