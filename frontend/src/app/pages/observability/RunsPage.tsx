import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Loader2, Search } from '@/components/icons'
import { useObservabilityRuns } from '@/core/observability'
import { formatDuration } from '@/lib/format'
import { cn } from '@/lib/utils'

import { formatAgo, formatCostList, formatTokens } from '@/components/observability/format'
import { runStatusMeta } from '@/components/observability/statusMeta'

/**
 * 运行记录页（app/pages/observability/RunsPage）
 *
 * 职责：观测台运行列表——关键词搜索 / 时间窗口（含全部）/ 状态筛选 + 真实分页。
 *       表格 table-fixed 定宽 + 相对时间，避免长请求/绝对时间把列撑爆横向溢出（对齐模板）。
 */

const PAGE_SIZE = 15

/* 表格单元格基础样式（对齐模板：表头小字灰、左对齐；数据行 12.5px + 细分隔线） */
const TH = 'px-3.5 py-2 text-[11.5px] font-normal text-muted-foreground'
const TD = 'px-3.5 py-[7px] text-[12.5px] border-b border-border/60 align-middle'

const STATUSES = [
  { value: '', label: '全部' },
  { value: 'finished', label: '已完成' },
  { value: 'error', label: '失败' },
  { value: 'running', label: '运行中' },
  { value: 'cancelled', label: '已取消' },
]

export function RunsPage() {
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [status, setStatus] = useState('')
  const [range, setRange] = useState('7d')
  const [page, setPage] = useState(0)

  // 1.搜索防抖 300ms，避免每个按键都打一次后端
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300)
    return () => clearTimeout(t)
  }, [q])

  const { data, isLoading, isError, error } = useObservabilityRuns({
    status: status || undefined,
    q: debouncedQ || undefined,
    range: range as 'today' | '7d' | '30d' | 'all',
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  })

  const runs = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="mx-auto w-full max-w-[1240px] px-8 pt-[26px] pb-10">
      {/* 1.page-head：标题 + 副标题纵向堆叠（对齐模板） */}
      <div className="mb-5">
        <h1 className="font-display text-[21px] font-semibold">运行记录</h1>
        <p className="mt-0.5 text-[12.5px] text-muted-foreground">
          全部用户的 agent 运行 · 点击行查看完整链路
        </p>
      </div>

      {/* 2.筛选条 */}
      <div className="mb-4 flex flex-wrap items-center gap-2.5">
        <div className="flex min-w-[250px] items-center gap-2 rounded-[10px] border bg-card px-3 py-1.5">
          <Search className="size-3.5 shrink-0 text-muted-foreground" />
          <input
            value={q}
            onChange={(e) => {
              setQ(e.target.value)
              setPage(0)
            }}
            placeholder="搜索请求内容 / trace_id / run_id"
            className="w-full bg-transparent text-[13px] outline-none"
          />
        </div>
        <select
          value={range}
          onChange={(e) => {
            setRange(e.target.value)
            setPage(0)
          }}
          className="rounded-[10px] border bg-card px-2.5 py-1.5 text-[12.5px] text-muted-foreground outline-none"
        >
          <option value="today">今天</option>
          <option value="7d">近 7 天</option>
          <option value="30d">近 30 天</option>
          <option value="all">全部</option>
        </select>
        <div className="flex gap-0.5">
          {STATUSES.map((s) => (
            <button
              key={s.value}
              type="button"
              onClick={() => {
                setStatus(s.value)
                setPage(0)
              }}
              className={cn(
                'rounded-full px-3 py-1 text-[12.5px] transition-colors',
                status === s.value
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
              )}
            >
              {s.label}
            </button>
          ))}
        </div>
        <span className="ml-auto text-[12px] text-muted-foreground">匹配 {total} 条</span>
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
      {!isLoading && !isError && runs.length === 0 && (
        <div className="py-16 text-center text-sm text-muted-foreground">暂无运行记录</div>
      )}

      {!isLoading && !isError && runs.length > 0 && (
        <div className="overflow-hidden rounded-lg border bg-card">
          <table className="w-full table-fixed">
            {/* colgroup 定宽（34% 请求列 truncate，其余按内容分配） */}
            <colgroup>
              <col className="w-[34%]" />
              <col className="w-[11%]" />
              <col className="w-[10%]" />
              <col className="w-[14%]" />
              <col className="w-[9%]" />
              <col className="w-[9%]" />
              <col className="w-[8%]" />
              <col className="w-[10%]" />
            </colgroup>
            <thead>
              <tr className="border-b">
                <th className={cn(TH, 'text-left')}>请求</th>
                <th className={cn(TH, 'text-left')}>状态</th>
                <th className={cn(TH, 'text-left')}>用户</th>
                <th className={cn(TH, 'text-left')}>模型</th>
                <th className={cn(TH, 'text-right')}>Token</th>
                <th className={cn(TH, 'text-right')}>成本</th>
                <th className={cn(TH, 'text-right')}>耗时</th>
                <th className={cn(TH, 'text-right')}>时间</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const meta = runStatusMeta(r.status)
                const dur =
                  r.started_at != null && r.finished_at != null ? r.finished_at - r.started_at : null
                return (
                  <tr
                    key={r.run_id}
                    className="cursor-pointer transition-colors hover:bg-secondary/50"
                    onClick={() => navigate(`/observability/runs/${r.run_id}`)}
                  >
                    <td className={cn(TD, 'truncate')} title={r.input_preview}>
                      {r.input_preview || r.run_id}
                    </td>
                    <td className={TD}>
                      <span className="inline-flex items-center gap-1.5">
                        <span
                          className={cn(
                            'size-[7px] rounded-full',
                            r.status === 'finished' && 'bg-success',
                            r.status === 'error' && 'bg-destructive',
                            r.status === 'running' && 'bg-primary',
                            r.status === 'cancelled' && 'bg-[#b9b1a2]',
                            r.status === 'pending' && 'bg-[#c08a3e]',
                          )}
                        />
                        <span
                          className={cn(
                            r.status === 'finished' && 'text-success',
                            r.status === 'error' && 'text-destructive',
                            r.status === 'running' && 'text-primary',
                          )}
                        >
                          {meta.label}
                        </span>
                      </span>
                    </td>
                    <td className={TD}>
                      <span className="rounded-full bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
                        {r.user_id}
                      </span>
                    </td>
                    <td className={cn(TD, 'truncate font-mono text-xs text-muted-foreground')} title={r.model_name}>
                      {r.model_name}
                    </td>
                    <td className={cn(TD, 'text-right font-mono text-xs')}>{formatTokens(r.total_tokens)}</td>
                    <td className={cn(TD, 'text-right font-mono text-xs text-primary')}>
                      {formatCostList(r.cost)}
                    </td>
                    <td className={cn(TD, 'text-right font-mono text-xs text-muted-foreground')}>
                      {dur != null ? formatDuration(dur) : '—'}
                    </td>
                    <td className={cn(TD, 'whitespace-nowrap text-right text-muted-foreground')}>
                      {formatAgo(r.created_at)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          {/* 分页 */}
          <div className="flex items-center justify-between border-t px-4 py-2.5 text-[12.5px] text-muted-foreground">
            <span>
              共 {total} 条 · 每页 {PAGE_SIZE} 条
            </span>
            <div className="flex items-center gap-1">
              <button
                disabled={page === 0}
                onClick={() => setPage(page - 1)}
                className="min-w-[28px] rounded-lg px-2 py-1 transition-colors hover:bg-secondary disabled:opacity-40"
              >
                ‹
              </button>
              {pageButtons(page + 1, totalPages).map((p, i) =>
                p === '…' ? (
                  <span key={`e${i}`} className="px-1">
                    …
                  </span>
                ) : (
                  <button
                    key={p}
                    onClick={() => setPage(p - 1)}
                    className={cn(
                      'min-w-[28px] rounded-lg px-2 py-1 transition-colors',
                      p === page + 1
                        ? 'bg-primary font-semibold text-primary-foreground'
                        : 'hover:bg-secondary hover:text-foreground',
                    )}
                  >
                    {p}
                  </button>
                ),
              )}
              <button
                disabled={page + 1 >= totalPages}
                onClick={() => setPage(page + 1)}
                className="min-w-[28px] rounded-lg px-2 py-1 transition-colors hover:bg-secondary disabled:opacity-40"
              >
                ›
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/** 模板同款页码序列：1 2 3 … N（多于 7 页时首尾保留、中间窗口 + 省略号） */
function pageButtons(current: number, totalPages: number): Array<number | '…'> {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1)
  }
  const pages = new Set<number>([1, 2, current - 1, current, current + 1, totalPages - 1, totalPages])
  const sorted = [...pages].filter((p) => p >= 1 && p <= totalPages).sort((a, b) => a - b)
  const out: Array<number | '…'> = []
  let prev = 0
  for (const p of sorted) {
    if (p - prev > 1) out.push('…')
    out.push(p)
    prev = p
  }
  return out
}
