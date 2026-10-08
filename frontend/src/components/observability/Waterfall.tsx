import { useMemo } from 'react'

import type { SpanOut } from '@/core/observability/types'
import { cn } from '@/lib/utils'

import { displaySpanName, formatMs } from './format'
import { buildSpanTree, flattenSpanTree } from './tree'

/**
 * 调用时间线瀑布图（components/observability/Waterfall）
 *
 * 职责：Jaeger 式时间轴视图——横轴为时间，每条 span 一条横条，长度=耗时、位置=起止，
 *       一眼看出哪步慢、哪步失败。点击详情在「调用树」页签的主从面板里完成。
 */

const SPAN_COLOR: Record<string, string> = {
  llm: 'bg-primary',
  tool: 'bg-success',
  retrieval: 'bg-[#8a7a5c]',
  agent: 'bg-[#c08a3e]',
}

export function Waterfall({
  spans,
  runStart,
  runDuration,
}: {
  spans: SpanOut[]
  runStart: number
  runDuration: number
}) {
  // 1.组树后先序展平，保留层级缩进
  const flat = useMemo(() => flattenSpanTree(buildSpanTree(spans)), [spans])
  const total = runDuration > 0 ? runDuration : 1

  return (
    <div className="overflow-hidden rounded-lg border">
      {/* 表头：左列名 + 右时间轴刻度 */}
      <div className="grid grid-cols-[300px_1fr] border-b bg-secondary/45">
        <div className="px-3.5 py-1.5 text-[11.5px] text-muted-foreground">Span</div>
        <div className="relative h-[30px]">
          {[0, 0.25, 0.5, 0.75, 1].map((p) => (
            <span
              key={p}
              className="absolute top-0 bottom-0 border-l border-dashed border-border pl-1 text-[10px] text-muted-foreground"
              style={{ left: `${p * 100}%` }}
            >
              {(total * p).toFixed(1)}s
            </span>
          ))}
        </div>
      </div>

      {flat.map(({ span, depth }) => {
        // 2.相对位置：起点/宽度都相对 run 起点与总时长
        const left = runStart ? Math.max(0, ((span.started_at - runStart) / total) * 100) : 0
        const width =
          span.finished_at != null
            ? Math.max(0.6, ((span.finished_at - span.started_at) / total) * 100)
            : 0.6
        return (
          <div key={span.span_id} className="grid grid-cols-[300px_1fr] border-b last:border-b-0">
            <div className="flex min-w-0 items-center gap-2 px-3.5 py-2">
              <span className={cn('size-2 shrink-0 rounded-[2.5px]', SPAN_COLOR[span.type] ?? 'bg-primary')} />
              <span className="w-0 flex-shrink-0" style={{ width: depth * 14 }} />
              <span className="min-w-0 flex-1 truncate font-mono text-xs" title={displaySpanName(span.name, span.type, span.model_name)}>
                {displaySpanName(span.name, span.type, span.model_name)}
              </span>
              <span className="ml-auto shrink-0 font-mono text-[11px] text-muted-foreground">
                {formatMs(span.duration_ms)}
              </span>
            </div>
            <div
              className="relative min-h-[34px]"
              style={{
                backgroundImage:
                  'repeating-linear-gradient(90deg, var(--border) 0, var(--border) 1px, transparent 1px, transparent 25%)',
              }}
            >
              <div
                className={cn(
                  'absolute top-1/2 h-[14px] -translate-y-1/2 rounded',
                  span.error ? 'bg-destructive' : SPAN_COLOR[span.type] ?? 'bg-primary',
                )}
                style={{ left: `${left}%`, width: `${width}%` }}
              />
            </div>
          </div>
        )
      })}

      {/* 图例 */}
      <div className="flex flex-wrap gap-4 border-t px-3.5 py-2 text-[11.5px] text-muted-foreground">
        <span className="flex items-center gap-1.5"><span className="size-2 rounded bg-primary" />模型</span>
        <span className="flex items-center gap-1.5"><span className="size-2 rounded bg-success" />工具</span>
        <span className="flex items-center gap-1.5"><span className="size-2 rounded bg-[#8a7a5c]" />检索</span>
        <span className="flex items-center gap-1.5"><span className="size-2 rounded bg-[#c08a3e]" />子代理</span>
        <span className="flex items-center gap-1.5"><span className="size-2 rounded bg-destructive" />失败</span>
      </div>
    </div>
  )
}
