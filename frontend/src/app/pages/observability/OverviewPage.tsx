import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Loader2 } from '@/components/icons'
import { useObservabilityRuns, useObservabilityStats } from '@/core/observability'
import { cn } from '@/lib/utils'

import { RangePicker } from '@/components/observability/RangePicker'
import { TrendChart } from '@/components/observability/charts'
import { formatAgo, formatCost, formatTokens } from '@/components/observability/format'

/**
 * 总览页（app/pages/observability/OverviewPage）
 *
 * 职责：观测台第一屏——page-head（标题 + 副标题 + 右侧时间窗口）+ 核心指标卡（大数字 +
 *       小单位 + 涨跌 delta）+ 运行/成本趋势 + 模型调用占比 + 最近运行。
 *       严格对齐 doc/observability-template 的总览版式；数值渲染空安全。
 */

const RANGE_META: Record<string, { label: string; days: number }> = {
  today: { label: '今天', days: 1 },
  '7d': { label: '近 7 天', days: 7 },
  '30d': { label: '近 30 天', days: 30 },
}

/** 大数字 + 小单位拆分（>=1M 显示 M，>=1K 显示 K） */
function splitNum(n: number): { v: string; unit: string } {
  if (n >= 1_000_000) return { v: (n / 1_000_000).toFixed(2), unit: 'M' }
  if (n >= 1_000) return { v: (n / 1_000).toFixed(1), unit: 'K' }
  return { v: String(n), unit: '' }
}

const STATUS_DOT: Record<string, string> = {
  finished: 'bg-success',
  error: 'bg-destructive',
  running: 'bg-primary',
  cancelled: 'bg-[#b9b1a2]',
  pending: 'bg-[#c08a3e]',
}

export function OverviewPage() {
  const navigate = useNavigate()
  const [range, setRange] = useState('7d')
  const rangeMeta = RANGE_META[range] ?? RANGE_META['7d']!

  const { data: stats, isLoading, isError, error } = useObservabilityStats(range)
  const { data: recent } = useObservabilityRuns({
    limit: 6,
    range: range as 'today' | '7d' | '30d',
  })

  const ov = stats?.overview
  const prev = stats?.overview_prev ?? null
  const models = stats?.models ?? []
  const recentRuns = recent?.items ?? []
  const totalModelCost = models.reduce((acc, m) => acc + (m.cost ?? 0), 0)

  // 1.较上期涨跌（模板「▲12% 较上期」）：总运行看环比百分比，成功率看百分点差值；无上一窗口则不给 delta
  const totalPct = prev && prev.total > 0 ? (((ov?.total ?? 0) - prev.total) / prev.total) * 100 : null
  const rateDelta = prev ? (ov?.success_rate ?? 0) - (prev.success_rate ?? 0) : null

  return (
    <div className="mx-auto w-full max-w-[1240px] px-8 pt-[26px] pb-10">
      {/* 1.page-head：标题 + 副标题（左）+ 时间窗口（右） */}
      <div className="mb-5 flex items-end justify-between">
        <div>
          <h1 className="font-display text-[21px] font-semibold">总览</h1>
          <p className="mt-0.5 text-[12.5px] text-muted-foreground">
            全部用户 · {rangeMeta.label}
          </p>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </div>

      {isLoading && (
        <div className="flex justify-center py-16">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}
      {isError && (
        <div className="py-16 text-center text-sm text-destructive">
          {error instanceof Error ? error.message : '加载失败'}
        </div>
      )}

      {!isLoading && !isError && ov && (
        <>
          {/* 2.核心指标卡（大数字 + 小单位 + 副标题/涨跌，全空安全） */}
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Metric
              label="总运行"
              value={String(ov.total ?? 0)}
              sub={`${rangeMeta.label}累计`}
              delta={
                totalPct != null
                  ? { v: `${Math.abs(totalPct).toFixed(1)}% 较上期`, up: totalPct >= 0 }
                  : undefined
              }
            />
            <Metric
              label="成功率"
              value={(ov.success_rate ?? 0).toFixed(1)}
              unit="%"
              delta={
                rateDelta != null
                  ? { v: `${Math.abs(rateDelta).toFixed(1)}% 较上期`, up: rateDelta >= 0 }
                  : undefined
              }
            />
            <Metric
              label="失败 / 取消"
              value={String(ov.errors ?? 0)}
              unit={`/ ${ov.cancelled ?? 0}`}
              sub={`${rangeMeta.label}累计`}
            />
            <Metric
              label="总 Token"
              value={splitNum(ov.tokens ?? 0).v}
              unit={splitNum(ov.tokens ?? 0).unit}
              sub={`输入 ${formatTokens(ov.in_tokens)} · 输出 ${formatTokens(ov.out_tokens)}`}
            />
            <Metric
              label="总成本"
              value={formatCost(ov.cost)}
              sub={`日均 $${((ov.cost ?? 0) / rangeMeta.days).toFixed(4)}`}
            />
            <Metric
              label="P95 耗时"
              value={(ov.p95_duration ?? 0).toFixed(1)}
              unit="s"
              sub={`P50 ${(ov.p50_duration ?? 0).toFixed(1)}s · 活跃 ${ov.active ?? 0}`}
            />
          </div>

          {/* 3.趋势图 */}
          <div className="rounded-lg border bg-card p-4">
            <div className="flex items-center justify-between text-[13.5px] font-semibold">
              <span>运行与成本趋势</span>
              <span className="text-[11.5px] font-normal text-muted-foreground">
                {rangeMeta.label} · 柱 = 运行数 · 线 = 每日成本($)
              </span>
            </div>
            <TrendChart daily={stats?.daily ?? []} />
          </div>

          {/* 4.模型调用占比 + 最近运行 */}
          <div className="mt-4 grid grid-cols-1 gap-3.5 lg:grid-cols-2">
            {/* 模型调用占比 */}
            <div className="rounded-lg border bg-card p-4">
              <div className="flex items-center justify-between text-[13.5px] font-semibold">
                <span>模型调用占比</span>
                <span className="text-[11.5px] font-normal text-muted-foreground">{rangeMeta.label}</span>
              </div>
              <div className="mt-2">
                {models.length === 0 && (
                  <p className="py-4 text-center text-sm text-muted-foreground">暂无数据</p>
                )}
                {models.map((m) => {
                  const pct = totalModelCost > 0 ? ((m.cost ?? 0) / totalModelCost) * 100 : 0
                  return (
                    <div
                      key={m.model}
                      className="grid grid-cols-[140px_1fr_52px_72px] items-center gap-3 py-[7px] text-[12.5px]"
                    >
                      <span className="truncate font-mono text-xs" title={m.model}>
                        {m.model}
                      </span>
                      <div className="h-2 overflow-hidden rounded-full bg-secondary">
                        <div
                          className="h-full rounded-full bg-primary transition-[width] duration-500"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      <span className="text-right text-muted-foreground">{pct.toFixed(1)}%</span>
                      <span className="text-right font-mono text-xs text-primary">
                        {formatCost(m.cost)}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* 最近运行 */}
            <div className="rounded-lg border bg-card p-4">
              <div className="flex items-center justify-between text-[13.5px] font-semibold">
                <span>最近运行</span>
                <button
                  type="button"
                  onClick={() => navigate('/observability/runs')}
                  className="text-[11.5px] font-normal text-muted-foreground transition-colors hover:text-foreground"
                >
                  点击进入运行记录
                </button>
              </div>
              <div className="mt-1">
                {recentRuns.length === 0 && (
                  <p className="py-4 text-center text-sm text-muted-foreground">暂无运行记录</p>
                )}
                {recentRuns.map((r) => (
                  <button
                    key={r.run_id}
                    type="button"
                    onClick={() => navigate(`/observability/runs/${r.run_id}`)}
                    className="flex w-full items-center gap-2.5 border-b border-border/60 py-2 text-left text-[12.5px] last:border-b-0 transition-colors hover:bg-secondary/40"
                  >
                    <span
                      className={cn(
                        'size-[7px] shrink-0 rounded-full',
                        STATUS_DOT[r.status] ?? 'bg-muted-foreground',
                      )}
                    />
                    <span className="min-w-0 flex-1 truncate" title={r.input_preview}>
                      {r.input_preview || r.run_id}
                    </span>
                    <span className="shrink-0 rounded-full bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
                      {r.user_id}
                    </span>
                    <span className="shrink-0 text-muted-foreground">
                      {formatAgo(r.created_at)}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

/* ── 指标卡（对齐模板：label 小字 + 大数字 + 小单位 + 副标题或涨跌 delta） ── */
function Metric({
  label,
  value,
  unit,
  sub,
  delta,
}: {
  label: string
  value: string
  unit?: string
  sub?: string
  delta?: { v: string; up: boolean }
}) {
  return (
    <div className="relative overflow-hidden rounded-[0.9rem] border bg-card px-4 pb-3 pt-3.5">
      <div className="text-[11.5px] tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 font-display text-[24px] font-semibold leading-[1.1]">
        {value}
        {unit && <span className="ml-0.5 text-[12px] font-normal text-muted-foreground">{unit}</span>}
      </div>
      {delta ? (
        <div className={cn('mt-1 text-[11px] font-medium', delta.up ? 'text-success' : 'text-destructive')}>
          {delta.up ? '▲' : '▼'} {delta.v}
        </div>
      ) : sub ? (
        <div className="mt-1 text-[11px] text-muted-foreground">{sub}</div>
      ) : null}
    </div>
  )
}
