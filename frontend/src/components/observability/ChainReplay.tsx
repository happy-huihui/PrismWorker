import type { ChainEvent } from '@/core/observability/types'

import { cn } from '@/lib/utils'

/**
 * 思考链回放（components/observability/ChainReplay）
 *
 * 职责：把 run 的思考链事件流（runs.events，RunEvent 序列）还原成时间线——
 *       思考 / 叙述 / 工具调用 / 进度 / 结束，按到达顺序展示。
 */

interface ChainStep {
  kind: 'think' | 'narrate' | 'tool' | 'info' | 'answer' | 'error' | 'cancel'
  text: string
  meta?: string
}

/** 把单条 RunEvent 映射成展示步骤；无关事件返回 null 跳过 */
function stepOf(ev: ChainEvent): ChainStep | null {
  const d = ev.data ?? {}
  switch (ev.event) {
    case 'reasoning_chunk':
      return { kind: 'think', text: String(d.text ?? '') }
    case 'message_chunk':
      return { kind: 'narrate', text: String(d.text ?? '') }
    case 'tool_start':
      return {
        kind: 'tool',
        text: `调用 ${String(d.tool ?? '工具')}`,
        meta: d.description ? String(d.description) : undefined,
      }
    case 'tool_end':
      return {
        kind: 'tool',
        text: `${String(d.tool ?? '工具')} 结束`,
        meta: `${d.duration_seconds != null ? `${d.duration_seconds}s` : ''} · ${d.ok !== false ? '成功' : '失败'}`,
      }
    case 'prints': {
      const lines = Array.isArray(d.prints) ? d.prints.map(String) : []
      return lines.length ? { kind: 'info', text: lines.join('\n') } : null
    }
    case 'run_meta':
      return { kind: 'info', text: `使用模型 ${String(d.model_name ?? '')}` }
    case 'run_finished':
      // 取消与完成共用 run_finished 帧，靠 data.status 区分（cancelled → 已取消）
      return d.status === 'cancelled'
        ? { kind: 'cancel', text: '运行已取消', meta: `消息 ${String(d.message_count ?? 0)} 条` }
        : { kind: 'answer', text: '运行完成', meta: `消息 ${String(d.message_count ?? 0)} 条` }
    case 'run_error':
      return { kind: 'error', text: String(d.error ?? '运行出错') }
    default:
      return null
  }
}

const KIND_LABEL: Record<ChainStep['kind'], string> = {
  think: '思考',
  narrate: '叙述',
  tool: '工具',
  info: '进度',
  answer: '答复',
  error: '失败',
  cancel: '取消',
}

export function ChainReplay({ events }: { events: ChainEvent[] }) {
  const steps = events.map(stepOf).filter((s): s is ChainStep => s !== null)
  if (steps.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">暂无思考链数据</p>
  }
  return (
    <div className="py-1">
      {steps.map((s, i) => (
        <div
          key={i}
          className={cn(
            'relative ml-[18px] flex gap-3 border-l-2 border-border py-2 pl-3',
            s.kind === 'error' && 'border-l-destructive',
          )}
        >
          <span
            className={cn(
              'absolute -left-[5px] top-[14px] size-[8px] rounded-full bg-border',
              s.kind === 'think' && 'bg-primary',
              s.kind === 'tool' && 'bg-success',
              s.kind === 'error' && 'bg-destructive',
              s.kind === 'cancel' && 'bg-[#b9b1a2]',
            )}
          />
          <span className="w-[30px] shrink-0 pt-0.5 text-[11px] text-muted-foreground">
            {KIND_LABEL[s.kind]}
          </span>
          <div className="min-w-0">
            <div
              className={cn(
                'whitespace-pre-wrap text-[12.5px] leading-relaxed',
                (s.kind === 'think' || s.kind === 'narrate') && 'text-muted-foreground',
                s.kind === 'error' && 'text-destructive',
              )}
            >
              {s.text}
            </div>
            {s.meta && <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">{s.meta}</div>}
          </div>
        </div>
      ))}
    </div>
  )
}
