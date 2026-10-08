import { Fragment, useEffect, useMemo, useState } from 'react'

import { Loader2 } from '@/components/icons'
import { useObservabilityEvals } from '@/core/observability'
import type { EvalDetail, EvalSummary } from '@/core/observability/types'
import { formatAbsoluteTime } from '@/lib/format'
import { cn } from '@/lib/utils'

/**
 * 评测页（app/pages/observability/EvalsPage）
 *
 * 职责：三层结构展示离线评测分数（分数双存的本地侧，对齐 LangSmith/Braintrust 主流形态）——
 *       ① 实验记分板（每次评测一张卡：综合分/通过率/样本数）② 样本透视（行=样本，列=指标着色）
 *       ③ 钻取（点样本展开输出原文与各指标分）。
 */

// 六个指标（展示顺序固定）：前四通用，后二对应复杂/边界任务
const ALL_METRICS = ['correctness', 'faithfulness', 'tool_selection', 'trajectory', 'task_completion', 'safety_refusal'] as const
const METRIC_LABEL: Record<string, string> = {
  correctness: '正确性',
  faithfulness: '忠实度',
  tool_selection: '工具选择',
  trajectory: '轨迹质量',
  task_completion: '任务完成度',
  safety_refusal: '安全拒答',
}
// 通过阈值：单样本所有指标都 ≥ 此值才算「通过」
const PASS_THRESHOLD = 0.6

/* 样本透视表单元格基础样式（与运行记录/分析页同款：表头小字灰 + 行分隔线 + 统一内边距） */
const TH = 'px-3.5 py-2 text-[11.5px] font-normal text-muted-foreground'
const TD = 'px-3.5 py-[7px] text-[12.5px] border-b border-border/60 align-middle'

/** 0~1 分数 → 颜色（>=0.8 绿 / >=0.5 琥珀 / 其余红 / null 灰） */
function scoreTone(score: number | null | undefined): string {
  if (score == null) return 'text-muted-foreground'
  if (score >= 0.8) return 'text-success'
  if (score >= 0.5) return 'text-[#c08a3e]'
  return 'text-destructive'
}

interface ExperimentCard {
  name: string
  model: string
  latest: number
  metrics: Record<string, number>
}

/** 从 summary 聚合出实验列表（每个实验一张卡） */
function buildExperiments(summary: EvalSummary[]): ExperimentCard[] {
  const map = new Map<string, ExperimentCard>()
  for (const s of summary) {
    if (!map.has(s.experiment)) {
      map.set(s.experiment, { name: s.experiment, model: s.model_name, latest: s.latest, metrics: {} })
    }
    map.get(s.experiment)!.metrics[s.metric] = s.avg_score
  }
  return [...map.values()].sort((a, b) => b.latest - a.latest)
}

/** 综合分 = 已有指标的平均 */
function overall(metrics: Record<string, number>): number {
  const vals = Object.values(metrics)
  if (vals.length === 0) return 0
  return vals.reduce((a, b) => a + b, 0) / vals.length
}

/** 通过率 = 所有指标都 ≥ 阈值的样本占比 */
function passRate(exp: string, details: EvalDetail[]): number {
  const byQ = new Map<string, Record<string, number>>()
  for (const d of details) {
    if (d.experiment !== exp) continue
    if (!byQ.has(d.question)) byQ.set(d.question, {})
    byQ.get(d.question)![d.metric] = d.score
  }
  if (byQ.size === 0) return 0
  let passed = 0
  for (const scores of byQ.values()) {
    if (ALL_METRICS.every((m) => (scores[m] ?? 0) >= PASS_THRESHOLD)) passed++
  }
  return passed / byQ.size
}

export function EvalsPage() {
  const { data, isLoading, isError, error } = useObservabilityEvals()
  const summary = data?.summary ?? []
  const details = data?.details ?? []

  const experiments = useMemo(() => buildExperiments(summary), [summary])
  const [selected, setSelected] = useState<string | null>(null)
  // 默认选中最新实验
  useEffect(() => {
    if (!selected && experiments.length > 0) setSelected(experiments[0].name)
  }, [experiments, selected])

  const sel = experiments.find((e) => e.name === selected) ?? null

  // 选中实验的样本透视（行=样本，metrics+output）
  const samples = useMemo(() => {
    const map = new Map<string, { metrics: Record<string, number>; output: string }>()
    for (const d of details) {
      if (sel && d.experiment !== sel.name) continue
      if (!map.has(d.question)) map.set(d.question, { metrics: {}, output: d.output })
      map.get(d.question)!.metrics[d.metric] = d.score
    }
    return [...map.entries()].map(([question, v]) => ({ question, ...v }))
  }, [details, sel])

  const [expanded, setExpanded] = useState<string | null>(null)

  return (
    <div className="mx-auto w-full max-w-[1240px] px-8 pt-[26px] pb-10">
      {/* 1.标题 */}
      <div className="mb-5">
        <h1 className="font-display text-[21px] font-semibold">评测</h1>
        <p className="mt-0.5 text-[12.5px] text-muted-foreground">
          离线 golden 数据集评测 · 分数双存（LangSmith UI + 本地）· 由 python -m harness.eval 回写
        </p>
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
      {!isLoading && !isError && experiments.length === 0 && (
        <div className="py-16 text-center text-sm text-muted-foreground">
          暂无评测数据 —— 先跑 <span className="font-mono">python -m harness.eval</span> 回写分数
        </div>
      )}

      {!isLoading && !isError && experiments.length > 0 && (
        <>
          {/* 2.实验记分板 */}
          <div className="mb-4 flex flex-wrap gap-3">
            {experiments.map((e) => {
              const ov = overall(e.metrics)
              const pr = passRate(e.name, details)
              const active = e.name === selected
              return (
                <button
                  key={e.name}
                  type="button"
                  onClick={() => setSelected(e.name)}
                  className={cn(
                    'min-w-[220px] rounded-lg border bg-card p-3.5 text-left transition-colors',
                    active ? 'border-primary' : 'hover:border-muted-foreground/40',
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span className="min-w-0 truncate font-mono text-[12.5px] font-semibold" title={e.name}>
                      {e.name}
                    </span>
                    <span className={cn('font-display text-lg font-semibold', scoreTone(ov))}>
                      {ov.toFixed(2)}
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-[11.5px] text-muted-foreground">
                    <span className="rounded-full bg-secondary px-1.5 py-px font-mono">{e.model}</span>
                    <span>通过率 {(pr * 100).toFixed(0)}%</span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-muted-foreground">
                    {formatAbsoluteTime(e.latest)}
                  </div>
                </button>
              )
            })}
          </div>

          {/* 3.选中实验的汇总卡 + 样本透视 + 钻取 */}
          {sel && (
            <>
              {/* 汇总大数字卡 */}
              <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <SummaryCard label="综合分" value={overall(sel.metrics).toFixed(2)} />
                <SummaryCard label="通过率" value={`${(passRate(sel.name, details) * 100).toFixed(0)}%`} />
                <SummaryCard label="样本数" value={String(samples.length)} />
                <SummaryCard
                  label="最差指标"
                  value={METRIC_LABEL[worstMetric(sel.metrics)] ?? '—'}
                  tone="bad"
                />
              </div>

              {/* 样本透视表（table-fixed + colgroup 定宽，样本列 truncate 防撑爆） */}
              <div className="overflow-hidden rounded-lg border bg-card">
                <table className="w-full table-fixed">
                  <colgroup>
                    <col className="w-[36%]" />
                    {ALL_METRICS.map((m) => (
                      <col key={m} className="w-[16%]" />
                    ))}
                  </colgroup>
                  <thead>
                    <tr className="border-b">
                      <th className={cn(TH, 'text-left')}>样本</th>
                      {ALL_METRICS.map((m) => (
                        <th key={m} className={cn(TH, 'text-right')}>{METRIC_LABEL[m]}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {samples.map((s) => (
                      <Fragment key={s.question}>
                        <tr
                          className="cursor-pointer transition-colors hover:bg-secondary/40"
                          onClick={() => setExpanded(expanded === s.question ? null : s.question)}
                        >
                          <td className={cn(TD, 'truncate')} title={s.question}>
                            {s.question}
                          </td>
                          {ALL_METRICS.map((m) => (
                            <td key={m} className={cn(TD, 'text-right font-mono', scoreTone(s.metrics[m]))}>
                              {s.metrics[m] == null ? '—' : s.metrics[m].toFixed(2)}
                            </td>
                          ))}
                        </tr>
                        {/* 钻取：展开看输出原文 + 各指标分 */}
                        {expanded === s.question && (
                          <tr className="bg-secondary/20">
                            <td colSpan={ALL_METRICS.length + 1} className="border-b border-border/60 px-3.5 py-3">
                              <div className="mb-1 text-[11px] text-muted-foreground">Agent 输出</div>
                              <div className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg border bg-background p-3 font-mono text-[12px] leading-relaxed">
                                {s.output || '(空)'}
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}

function worstMetric(metrics: Record<string, number>): string {
  let worst = ''
  let min = Infinity
  for (const [m, v] of Object.entries(metrics)) {
    if (v < min) {
      min = v
      worst = m
    }
  }
  return worst
}

function SummaryCard({ label, value, tone }: { label: string; value: string; tone?: 'bad' }) {
  return (
    <div className="rounded-[0.9rem] border bg-card px-4 pb-3 pt-3.5">
      <div className="text-[11.5px] tracking-wide text-muted-foreground">{label}</div>
      <div className={cn('mt-1 font-display text-[24px] font-semibold leading-[1.1]', tone === 'bad' && 'text-destructive')}>
        {value}
      </div>
    </div>
  )
}
