import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Loader2 } from '@/components/icons'
import { useObservabilityAnalytics } from '@/core/observability'
import { cn } from '@/lib/utils'

import { RangePicker } from '@/components/observability/RangePicker'
import { CostChart } from '@/components/observability/charts'
import { formatCost, formatCostList, formatMs, formatTokens } from '@/components/observability/format'

/**
 * 分析页（app/pages/observability/AnalyticsPage）
 *
 * 职责：模型聚合表 / 工具排行 / 每日成本 / 最贵 Top N，数据来自 GET /observability/analytics。
 *       模型表 table-fixed 定宽避免横向溢出；成本口径与模板对齐（表内 3 位、Top5 列表 4 位）。
 */

const RANGE_LABEL: Record<string, string> = {
  today: '今天',
  '7d': '近 7 天',
  '30d': '近 30 天',
}

/* 模型表单元格基础样式（对齐模板：表头小字灰左对齐、数值列右对齐 + 行分隔线） */
const TH = 'px-3.5 py-2 text-[11.5px] font-normal text-muted-foreground'
const TD = 'px-3.5 py-[7px] text-[12.5px]'

export function AnalyticsPage() {
  const navigate = useNavigate()
  const [range, setRange] = useState('7d')
  const { data, isLoading, isError, error } = useObservabilityAnalytics(range)

  const models = data?.models ?? []
  const tools = data?.tools ?? []
  const maxToolCalls = Math.max(1, ...tools.map((t) => t.calls))
  const rangeLabel = RANGE_LABEL[range] ?? RANGE_LABEL['7d']!

  return (
    <div className="mx-auto w-full max-w-[1240px] px-8 pt-[26px] pb-10">
      {/* 1.page-head：标题 + 副标题（左）+ 时间窗口（右） */}
      <div className="mb-5 flex items-end justify-between">
        <div>
          <h1 className="font-display text-[21px] font-semibold">分析</h1>
          <p className="mt-0.5 text-[12.5px] text-muted-foreground">
            模型 / 工具 / 成本 · {rangeLabel} · 全部用户
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

      {!isLoading && !isError && data && (
        <>
          {/* 2.模型聚合表（table-fixed 定宽，模型列 truncate 防撑爆） */}
          <div className="overflow-hidden rounded-lg border bg-card">
            <div className="px-4 pt-3.5 text-[13.5px] font-semibold">模型分析</div>
            <table className="mt-2 w-full table-fixed">
              <thead>
                <tr className="border-b">
                  <th className={cn(TH, 'w-[28%] text-left')}>模型</th>
                  <th className={cn(TH, 'text-right')}>调用</th>
                  <th className={cn(TH, 'text-right')}>输入 Token</th>
                  <th className={cn(TH, 'text-right')}>输出 Token</th>
                  <th className={cn(TH, 'text-right')}>成本</th>
                  <th className={cn(TH, 'text-right')}>平均耗时</th>
                </tr>
              </thead>
              <tbody>
                {models.length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-6 text-center text-muted-foreground">
                      暂无数据
                    </td>
                  </tr>
                )}
                {models.map((m) => (
                  <tr key={m.model} className="border-t">
                    <td className={cn(TD, 'truncate font-mono text-xs')} title={m.model}>
                      {m.model}
                    </td>
                    <td className={cn(TD, 'text-right')}>{m.calls}</td>
                    <td className={cn(TD, 'text-right font-mono text-xs text-muted-foreground')}>
                      {formatTokens(m.in_tokens)}
                    </td>
                    <td className={cn(TD, 'text-right font-mono text-xs text-muted-foreground')}>
                      {formatTokens(m.out_tokens)}
                    </td>
                    <td className={cn(TD, 'text-right font-mono text-xs text-primary')}>{formatCost(m.cost)}</td>
                    <td className={cn(TD, 'text-right font-mono text-xs text-muted-foreground')}>
                      {formatMs(m.avg_duration_ms)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
            {/* 3.工具排行 */}
            <div className="rounded-lg border bg-card p-4">
              <div className="flex items-center justify-between text-[13.5px] font-semibold">
                <span>工具调用排行</span>
                <span className="text-[11.5px] font-normal text-muted-foreground">次数 · 成功率</span>
              </div>
              <div className="mt-3">
                {tools.length === 0 && <p className="py-4 text-center text-sm text-muted-foreground">暂无数据</p>}
                {tools.map((t) => (
                  <div key={t.tool} className="grid grid-cols-[150px_1fr_64px_52px] items-center gap-3 py-1.5">
                    <span className="truncate font-mono text-xs" title={t.tool}>
                      {t.tool}
                    </span>
                    <div className="h-2 overflow-hidden rounded-full bg-secondary">
                      <div
                        className={cn('h-full rounded-full', t.success_rate < 0.8 ? 'bg-[#c08a3e]' : 'bg-success')}
                        style={{ width: `${(t.calls / maxToolCalls) * 100}%` }}
                      />
                    </div>
                    <span className="text-right text-muted-foreground">{t.calls} 次</span>
                    <span
                      className={cn(
                        'text-right font-mono text-xs',
                        t.success_rate < 0.8 ? 'text-[#c08a3e]' : 'text-success',
                      )}
                    >
                      {(t.success_rate * 100).toFixed(0)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* 4.成本趋势 + 最贵 Top N */}
            <div className="flex flex-col gap-4">
              <div className="rounded-lg border bg-card p-4">
                <div className="flex items-center justify-between text-[13.5px] font-semibold">
                  <span>每日成本</span>
                  <span className="text-[11.5px] font-normal text-muted-foreground">{rangeLabel}($)</span>
                </div>
                <CostChart daily={data.daily} />
              </div>

              <div className="rounded-lg border bg-card p-4">
                <div className="flex items-center justify-between text-[13.5px] font-semibold">
                  <span>最贵运行 Top 5</span>
                  <span className="text-[11.5px] font-normal text-muted-foreground">点击下钻</span>
                </div>
                <div className="mt-2">
                  {(data.top_cost ?? []).length === 0 && (
                    <p className="py-4 text-center text-sm text-muted-foreground">暂无数据</p>
                  )}
                  {(data.top_cost ?? []).map((r) => (
                    <button
                      key={r.run_id}
                      type="button"
                      onClick={() => navigate(`/observability/runs/${r.run_id}`)}
                      className="flex w-full items-center justify-between gap-2 border-b border-border/60 py-2 text-left text-[12.5px] last:border-b-0 hover:text-primary"
                    >
                      <span className="min-w-0 flex-1 truncate">{r.input_preview || r.run_id}</span>
                      <span className="shrink-0 rounded-full bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
                        {r.user_id}
                      </span>
                      <span className="shrink-0 font-mono text-xs font-semibold text-primary">
                        {formatCostList(r.cost)}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
