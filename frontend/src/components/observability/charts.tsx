import type { DailyPoint } from '@/core/observability/types'

/**
 * 观测台图表（components/observability/charts）
 *
 * 职责：总览/分析页的轻量 SVG 图表（无第三方图表库，用 currentColor 继承主题色）。
 */

/** 运行数（柱）+ 每日成本（线）组合趋势图 */
export function TrendChart({ daily }: { daily: DailyPoint[] }) {
  const W = 900
  const H = 220
  const padL = 40
  const padB = 26
  const padT = 14
  const maxRuns = Math.max(1, ...daily.map((d) => d.runs)) * 1.15
  const maxCost = Math.max(0.01, ...daily.map((d) => d.cost)) * 1.2
  const iw = W - padL - 16
  const ih = H - padT - padB
  const x = (i: number) => (daily.length <= 1 ? padL : padL + (i * iw) / (daily.length - 1))
  const yRuns = (v: number) => padT + ih - (v / maxRuns) * ih
  const yCost = (v: number) => padT + ih - (v / maxCost) * ih

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 w-full">
      {/* 网格 */}
      {[0, 1, 2, 3].map((i) => {
        const y = padT + (ih * i) / 3
        return <line key={i} x1={padL} y1={y} x2={W - 16} y2={y} className="stroke-border" />
      })}
      {/* 柱 = 运行数 */}
      <g className="text-[#d9cbb2]">
        {daily.map((d, i) => {
          const bw = 14
          return (
            <rect
              key={d.day}
              x={x(i) - bw / 2}
              y={yRuns(d.runs)}
              width={bw}
              height={padT + ih - yRuns(d.runs)}
              rx={3.5}
              fill="currentColor"
            >
              <title>{`${d.day} · ${d.runs} 次 · $${d.cost.toFixed(2)}`}</title>
            </rect>
          )
        })}
      </g>
      {/* 线 = 成本 */}
      <g className="text-primary">
        <polyline
          points={daily.map((d, i) => `${x(i)},${yCost(d.cost)}`).join(' ')}
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
        />
        {daily.map((d, i) => (
          <circle key={d.day} cx={x(i)} cy={yCost(d.cost)} r={3} fill="currentColor" />
        ))}
      </g>
      {/* x 轴标签：模板同款 D-N / 今天（隔一个显示，最后一天固定「今天」） */}
      {daily.map((d, i) =>
        i % 2 === 0 || i === daily.length - 1 ? (
          <text
            key={d.day}
            x={x(i)}
            y={H - 8}
            textAnchor="middle"
            fontSize={10}
            className="fill-muted-foreground"
          >
            {i === daily.length - 1 ? '今天' : `D-${daily.length - 1 - i}`}
          </text>
        ) : null,
      )}
      <text x={padL} y={padT + 4} fontSize={10} className="fill-muted-foreground">
        {Math.round(maxRuns)}
      </text>
    </svg>
  )
}

/** 每日成本折线 + 面积 */
export function CostChart({ daily }: { daily: DailyPoint[] }) {
  const W = 460
  const H = 150
  const padL = 34
  const padB = 20
  const padT = 10
  const max = Math.max(0.01, ...daily.map((d) => d.cost)) * 1.2
  const iw = W - padL - 12
  const ih = H - padT - padB
  const x = (i: number) => (daily.length <= 1 ? padL : padL + (i * iw) / (daily.length - 1))
  const y = (v: number) => padT + ih - (v / max) * ih
  const line = daily.map((d, i) => `${x(i)},${y(d.cost)}`).join(' ')
  const area = `${padL},${padT + ih} ${line} ${x(daily.length - 1)},${padT + ih}`

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="mt-1.5 w-full">
      <polygon points={area} className="fill-primary/10" />
      <polyline points={line} fill="none" className="stroke-primary" strokeWidth={2} />
      {daily.map((d, i) => (
        <circle key={d.day} cx={x(i)} cy={y(d.cost)} r={2.5} className="fill-primary">
          <title>{`$${d.cost.toFixed(2)}`}</title>
        </circle>
      ))}
      <line x1={padL} y1={padT + ih} x2={W - 12} y2={padT + ih} className="stroke-border" />
      <text x={padL} y={padT + 4} fontSize={9} className="fill-muted-foreground">
        ${max.toFixed(2)}
      </text>
    </svg>
  )
}
