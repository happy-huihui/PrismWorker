import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'

import { ArrowLeft, Loader2 } from '@/components/icons'
import { useObservabilityRun } from '@/core/observability'
import type { ChainEvent, SpanOut } from '@/core/observability/types'
import { formatAbsoluteTime, formatDuration } from '@/lib/format'
import { cn } from '@/lib/utils'

import { ChainReplay } from '@/components/observability/ChainReplay'
import { LogStream } from '@/components/observability/LogStream'
import { SpanTree } from '@/components/observability/SpanTree'
import { Waterfall } from '@/components/observability/Waterfall'
import { formatCost, formatTokens } from '@/components/observability/format'
import { runStatusMeta } from '@/components/observability/statusMeta'

/**
 * 运行详情页（app/pages/observability/RunDetailPage）
 *
 * 职责：观测台运行详情——概览卡（hero + 细分）+ 调用树（主从）/ 时间线 / 日志流 /
 *       思考链四页签。真实数据来自 GET /observability/runs/{id}（span 富属性已含原文）。
 */

type DTab = 'tree' | 'waterfall' | 'logs' | 'chain'
const DTABS: Array<{ key: DTab; label: string }> = [
  { key: 'tree', label: '调用树' },
  { key: 'waterfall', label: '时间线' },
  { key: 'logs', label: '日志流' },
  { key: 'chain', label: '思考链' },
]

export function RunDetailPage() {
  const { runId } = useParams()
  const navigate = useNavigate()
  const [tab, setTab] = useState<DTab>('tree')
  const { data, isLoading, isError, error } = useObservabilityRun(runId ?? '')

  const agg = useMemo(() => aggregate(data?.spans ?? []), [data])
  const meta = useMemo(() => extractRunMeta(data?.chain_events ?? []), [data])

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    )
  }
  if (isError || !data) {
    return (
      <div className="p-8 text-center text-sm text-destructive">
        {error instanceof Error ? error.message : '加载失败'}
      </div>
    )
  }

  const run = data.run
  const statusMeta = runStatusMeta(run.status)
  const duration =
    run.started_at != null && run.finished_at != null ? run.finished_at - run.started_at : null
  const runStart = run.started_at ?? data.spans[0]?.started_at ?? 0

  const copyTrace = () => {
    navigator.clipboard?.writeText(run.trace_id).then(() => toast.success('已复制 trace_id'))
  }

  return (
    <div className="mx-auto w-full max-w-[1240px] px-8 pt-[26px] pb-10">
      {/* 1.返回 + 标题 */}
      <button
        type="button"
        onClick={() => navigate('/observability/runs')}
        className="mb-4 flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-4" />
        返回运行记录
      </button>
      <div className="mb-4">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="inline-flex items-center gap-1.5">
            <span
              className={cn(
                'size-[7px] rounded-full',
                run.status === 'finished' && 'bg-success',
                run.status === 'error' && 'bg-destructive',
                run.status === 'running' && 'bg-primary',
                run.status === 'cancelled' && 'bg-[#b9b1a2]',
              )}
            />
            <span
              className={cn(
                run.status === 'finished' && 'text-success',
                run.status === 'error' && 'text-destructive',
                run.status === 'running' && 'text-primary',
              )}
            >
              {statusMeta.label}
            </span>
          </span>
          <h1 className="min-w-0 font-display text-[17px] font-semibold">
            {run.input_preview || run.run_id}
          </h1>
          <button
            type="button"
            onClick={copyTrace}
            className="rounded-md border px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            复制 trace_id
          </button>
        </div>
        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-[12px] text-muted-foreground">
          <span>run_id: {run.run_id}</span>
          <span>trace_id: {run.trace_id}</span>
          <span>用户: {run.user_id}</span>
          <span>线程: {run.thread_id}</span>
          <span>开始: {run.started_at != null ? formatAbsoluteTime(run.started_at) : '—'}</span>
        </div>
      </div>

      {/* 2.概览卡：hero 4 项 + 细分 12 项 */}
      <div className="mb-4 overflow-hidden rounded-lg border bg-card">
        <div className="grid grid-cols-2 border-b sm:grid-cols-4">
          <HeroCell k="状态" v={statusMeta.label} tone={run.status === 'error' ? 'bad' : run.status === 'finished' ? 'ok' : undefined} />
          <HeroCell k="总耗时" v={duration != null ? formatDuration(duration) : '—'} />
          <HeroCell k="模型" v={run.model_name} sub={meta.routingReason} />
          <HeroCell k="总成本" v={formatCost(run.cost)} sub={`${formatTokens(agg.tokens)} token`} />
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4">
          <Cell k="输入 Token" v={formatTokens(agg.inTokens)} />
          <Cell k="输出 Token" v={formatTokens(agg.outTokens)} />
          <Cell k="思考 Token" v={formatTokens(agg.reasoningTokens)} />
          <Cell k="深度思考" v={meta.thinking ? '已开启' : '关闭'} />
          <Cell k="LLM 调用" v={`${agg.llmCalls} 次`} />
          <Cell k="工具调用" v={`${agg.toolCalls} 次`} />
          <Cell k="子代理" v={`${agg.agentCalls} 个`} />
          <Cell k="消息数" v={`${run.message_count} 条`} />
          <Cell k="产物" v={run.artifacts.length ? run.artifacts.join(', ') : '—'} />
          <Cell k="开始" v={run.started_at != null ? formatAbsoluteTime(run.started_at) : '—'} />
          <Cell k="结束" v={run.finished_at != null ? formatAbsoluteTime(run.finished_at) : '—'} />
          <Cell k="trace_id" v={run.trace_id} mono />
        </div>
      </div>

      {/* 3.失败信息 */}
      {run.error && (
        <div className="mb-4 rounded-lg border border-destructive/25 bg-destructive/5 p-3 text-sm text-destructive">
          <div className="flex items-center gap-1.5 font-semibold">✕ 运行失败</div>
          <pre className="mt-1.5 whitespace-pre-wrap font-mono text-[11.5px]">{run.error}</pre>
        </div>
      )}

      {/* 4.页签 */}
      <div className="mb-3 flex gap-0.5 border-b">
        {DTABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={cn(
              'border-b-2 px-4 py-2 text-[13px] transition-colors',
              tab === t.key
                ? 'border-primary font-semibold text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'tree' && <SpanTree spans={data.spans} />}
      {tab === 'waterfall' && (
        <Waterfall spans={data.spans} runStart={runStart} runDuration={duration ?? 1} />
      )}
      {tab === 'logs' && (
        <div className="overflow-hidden rounded-lg border bg-card">
          <LogStream logs={data.logs} />
        </div>
      )}
      {tab === 'chain' && (
        <div className="overflow-hidden rounded-lg border bg-card">
          <ChainReplay events={data.chain_events} />
        </div>
      )}
    </div>
  )
}

function HeroCell({
  k,
  v,
  sub,
  tone,
}: {
  k: string
  v: string
  sub?: string
  tone?: 'ok' | 'bad'
}) {
  return (
    <div className="border-r px-5 py-4 last:border-r-0">
      <div className="text-[11.5px] text-muted-foreground">{k}</div>
      <div
        className={cn(
          'mt-1 font-display text-[20px] font-semibold leading-tight',
          tone === 'ok' && 'text-success',
          tone === 'bad' && 'text-destructive',
        )}
      >
        {v}
      </div>
      {sub && <div className="mt-0.5 text-[11px] text-muted-foreground">{sub}</div>}
    </div>
  )
}

function Cell({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="border-r border-b px-5 py-2.5 last:border-r-0 [&:nth-child(4n)]:border-r-0">
      <div className="text-[11px] text-muted-foreground">{k}</div>
      <div className={cn('mt-0.5 truncate text-[13px] font-semibold', mono && 'font-mono text-[11.5px]')} title={v}>
        {v}
      </div>
    </div>
  )
}

/** 从 spans 汇总 run 级指标（token 三路 + 各类调用次数） */
function aggregate(spans: SpanOut[]) {
  let inTokens = 0
  let outTokens = 0
  let reasoningTokens = 0
  let tokens = 0
  let llmCalls = 0
  let toolCalls = 0
  let agentCalls = 0
  for (const s of spans) {
    if (s.type === 'llm') {
      llmCalls++
      inTokens += s.input_tokens ?? 0
      outTokens += s.output_tokens ?? 0
      reasoningTokens += s.attributes.reasoning_tokens ?? 0
      tokens += s.total_tokens ?? 0
    } else if (s.type === 'tool') {
      toolCalls++
    } else if (s.type === 'agent') {
      agentCalls++
    }
  }
  return { inTokens, outTokens, reasoningTokens, tokens, llmCalls, toolCalls, agentCalls }
}

/** 从思考链事件里取 run_meta（深度思考 + 路由原因） */
function extractRunMeta(events: ChainEvent[]) {
  const metaEv = events.find((e) => e.event === 'run_meta')
  const d = (metaEv?.data ?? {}) as Record<string, unknown>
  const routing = (d.routing ?? {}) as Record<string, unknown>
  return {
    thinking: d.thinking_enabled === true,
    routingReason: typeof routing.reason === 'string' ? routing.reason : '',
  }
}
