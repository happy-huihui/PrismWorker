import { useEffect, useState, type RefObject } from 'react'
import { cn } from '@/lib/utils'

/**
 * 会话轮次快速索引（TurnIndex）
 *
 * 职责：在对话滚动容器左缘、垂直居中处渲染一列刻度——每个用户轮次一条，
 *      点击滚动到该轮，随滚动高亮当前可见轮次（对齐 qoder 的 minimap 交互）。
 * 依赖：轮次锚点由 MessageBubble 的 data-turn 提供；turnCount 变化时重新测量。
 */
interface TurnIndexProps {
  /** 对话滚动容器（overflow-y-auto 的那个元素） */
  scrollRef: RefObject<HTMLElement | null>
  /** 轮次总数（= 用户消息数） */
  turnCount: number
}

export function TurnIndex({ scrollRef, turnCount }: TurnIndexProps) {
  const [active, setActive] = useState(0)

  // 滚动时：把"顶部越过阈值的最靠下轮次"记为当前轮次
  useEffect(() => {
    const el = scrollRef.current
    if (!el || turnCount === 0) return
    const measure = () => {
      const nodes = Array.from(el.querySelectorAll<HTMLElement>('[data-turn]'))
      const cTop = el.getBoundingClientRect().top
      let idx = 0
      nodes.forEach((n, i) => {
        if (n.getBoundingClientRect().top - cTop <= 80) idx = i
      })
      setActive(idx)
    }
    measure()
    el.addEventListener('scroll', measure, { passive: true })
    return () => el.removeEventListener('scroll', measure)
  }, [scrollRef, turnCount])

  if (turnCount === 0) return null

  // 点击：滚动容器平滑滚到第 i 个轮次锚点（留 12px 顶部余量）
  const jump = (i: number) => {
    const el = scrollRef.current
    if (!el) return
    const node = Array.from(el.querySelectorAll<HTMLElement>('[data-turn]'))[i]
    if (!node) return
    el.scrollBy({
      top: node.getBoundingClientRect().top - el.getBoundingClientRect().top - 12,
      behavior: 'smooth',
    })
  }

  return (
    // 模板 .minimap：对话区最左缘竖排细刻度（无底无分隔），垂直居中、间距 10px
    <div className="pointer-events-none absolute top-5 bottom-5 left-3.5 z-10 flex flex-col items-center justify-center gap-2.5">
      {Array.from({ length: turnCount }).map((_, i) => (
        <button
          key={i}
          type="button"
          onClick={() => jump(i)}
          title={`第 ${i + 1} 轮`}
          className="group pointer-events-auto flex h-3 items-center"
        >
          <span
            className={cn(
              // 刻度 14x3px、2px 圆角；当前轮加长到 20px 并用主色（模板 .tick/.on）
              'h-[3px] rounded-[2px] transition-all',
              i === active
                ? 'w-5 bg-primary opacity-100'
                : 'w-3.5 bg-muted-foreground/30 opacity-85 group-hover:bg-primary group-hover:opacity-100',
            )}
          />
        </button>
      ))}
    </div>
  )
}
