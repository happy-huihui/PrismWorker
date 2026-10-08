import { useEffect, useMemo, useState } from 'react'

import type { SpanOut } from '@/core/observability/types'
import { cn } from '@/lib/utils'

import { displaySpanName, formatCost, formatMs, formatTokens, stripInputWrapper } from './format'
import { buildSpanTree, type SpanNode } from './tree'
import { spanTypeMeta } from './statusMeta'

/**
 * 调用树（components/observability/SpanTree）
 *
 * 职责：运行详情的主视图——左侧嵌套调用树（按 parent_span_id 组树）+ 右侧选中 span
 *       的详情面板（概览 / 输入 / 输出 / 参数四页签）。这是「系统描述整条链路」的核心。
 */

const SPAN_ICO: Record<string, string> = { llm: 'LLM', tool: 'T', retrieval: 'R', agent: 'A' }

function typeClass(type: string): string {
  return {
    llm: 'bg-primary',
    tool: 'bg-success',
    retrieval: 'bg-[#8a7a5c]',
    agent: 'bg-[#c08a3e]',
  }[type] ?? 'bg-primary'
}

export function SpanTree({ spans }: { spans: SpanOut[] }) {
  const roots = useMemo(() => buildSpanTree(spans), [spans])
  const [selectedId, setSelectedId] = useState<string | null>(spans[0]?.span_id ?? null)
  const selected = spans.find((s) => s.span_id === selectedId) ?? null

  if (spans.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">暂无 span 数据</p>
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_440px]">
      {/* 左：嵌套调用树 */}
      <div className="rounded-lg border p-1.5">
        {roots.map((n) => (
          <TreeNode key={n.span.span_id} node={n} depth={0} selectedId={selectedId} onSelect={setSelectedId} />
        ))}
      </div>
      {/* 右：选中 span 详情 */}
      <SpanDetail span={selected} />
    </div>
  )
}

function TreeNode({
  node,
  depth,
  selectedId,
  onSelect,
}: {
  node: SpanNode
  depth: number
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const { span } = node
  const hasTok = span.total_tokens != null && span.total_tokens > 0
  // 1.展示名兜底：库里的历史 span 名可能是 repr 碎片（llm.metadata={...}），这里清洗成标准名
  const dname = displaySpanName(span.name, span.type, span.model_name)
  return (
    <div className="border-b border-border/60 last:border-b-0">
      <button
        type="button"
        onClick={() => onSelect(span.span_id)}
        className={cn(
          'flex w-full items-center gap-2 rounded-lg py-1.5 pr-2 text-left text-[13px] transition-colors hover:bg-secondary/50',
          selectedId === span.span_id && 'bg-secondary',
        )}
        style={{ paddingLeft: 8 + depth * 16 }}
      >
        <span
          className={cn(
            'grid size-5 shrink-0 place-items-center rounded-md text-[10px] font-bold text-white',
            typeClass(span.type),
          )}
        >
          {SPAN_ICO[span.type] ?? spanTypeMeta(span.type).label.charAt(0)}
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-xs" title={dname}>
          {dname}
        </span>
        {hasTok && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {formatTokens(span.total_tokens)}
          </span>
        )}
        <span className="w-[46px] shrink-0 text-right font-mono text-[11px] text-muted-foreground">
          {formatMs(span.duration_ms)}
        </span>
        <span
          className={cn('size-[7px] shrink-0 rounded-full', span.error ? 'bg-destructive' : 'bg-success')}
        />
      </button>
      {node.children.map((c) => (
        <TreeNode key={c.span.span_id} node={c} depth={depth + 1} selectedId={selectedId} onSelect={onSelect} />
      ))}
    </div>
  )
}

/* ── 右侧 span 详情面板 ── */
type SdTab = 'overview' | 'input' | 'output' | 'params'
const SD_TABS: Array<{ key: SdTab; label: string }> = [
  { key: 'overview', label: '概览' },
  { key: 'input', label: '输入' },
  { key: 'output', label: '输出' },
  { key: 'params', label: '参数' },
]

function SpanDetail({ span }: { span: SpanOut | null }) {
  const [tab, setTab] = useState<SdTab>('overview')
  // 切换 span 时重置到概览页签
  useEffect(() => setTab('overview'), [span?.span_id])

  if (!span) {
    return <div className="rounded-lg border p-4 text-sm text-muted-foreground">选中左侧 span 查看详情</div>
  }

  const a = span.attributes
  const meta = spanTypeMeta(span.type)
  const reasoning = a.reasoning_tokens ?? 0
  const dname = displaySpanName(span.name, span.type, span.model_name)

  return (
    <div className="rounded-lg border lg:sticky lg:top-0">
      <div className="border-b px-4 py-3">
        <div className="flex items-center gap-2 font-mono text-[13px] font-semibold">
          <span className={cn('grid size-5 place-items-center rounded-md text-[10px] font-bold text-white', typeClass(span.type))}>
            {SPAN_ICO[span.type] ?? meta.label.charAt(0)}
          </span>
          {dname}
        </div>
        <div className="mt-1 text-[11.5px] text-muted-foreground">
          {meta.label} · {formatMs(span.duration_ms)} · {span.started_at != null ? `+${span.started_at.toFixed(2)}s` : ''}
        </div>
      </div>

      <div className="flex border-b">
        {SD_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={cn(
              'flex-1 border-b-2 py-2 text-[12.5px] transition-colors',
              tab === t.key ? 'border-primary font-semibold text-primary' : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="max-h-[60vh] overflow-y-auto px-4 py-3.5 text-[12.5px]">
        {tab === 'overview' && <OverviewTab span={span} reasoning={reasoning} />}
        {tab === 'input' && <InputTab span={span} />}
        {tab === 'output' && <OutputTab span={span} />}
        {tab === 'params' && <ParamsTab span={span} />}
      </div>
    </div>
  )
}

function Kv({ k, v, mono = true, tone }: { k: string; v: string; mono?: boolean; tone?: 'ok' | 'bad' }) {
  return (
    <>
      <span className="text-muted-foreground">{k}</span>
      <span className={cn('break-all', mono && 'font-mono', tone === 'ok' && 'text-success', tone === 'bad' && 'text-destructive')}>
        {v}
      </span>
    </>
  )
}

function OverviewTab({ span, reasoning }: { span: SpanOut; reasoning: number }) {
  return (
    <div className="grid grid-cols-[100px_1fr] gap-x-3 gap-y-1.5">
      <Kv k="类型" v={spanTypeMeta(span.type).label} mono={false} />
      {span.model_name && <Kv k="模型" v={span.model_name} />}
      <Kv k="状态" v={span.error ? '失败' : '成功'} mono={false} tone={span.error ? 'bad' : 'ok'} />
      <Kv k="耗时" v={formatMs(span.duration_ms)} />
      <Kv k="开始" v={span.started_at != null ? `+${span.started_at.toFixed(2)}s` : '—'} />
      <Kv k="结束" v={span.finished_at != null ? `+${span.finished_at.toFixed(2)}s` : '—'} />
      {span.attributes.finish_reason && <Kv k="finish_reason" v={span.attributes.finish_reason} />}
      {span.input_tokens != null && <Kv k="输入 Token" v={span.input_tokens.toLocaleString()} />}
      {span.output_tokens != null && <Kv k="输出 Token" v={span.output_tokens.toLocaleString()} />}
      {reasoning > 0 && <Kv k="思考 Token" v={reasoning.toLocaleString()} />}
      {span.cost != null && span.cost > 0 && <Kv k="成本" v={formatCost(span.cost)} />}
      {span.error && <Kv k="错误" v={span.error} tone="bad" />}
    </div>
  )
}

function InputTab({ span }: { span: SpanOut }) {
  const a = span.attributes
  // 1.模型调用：只展示 system + human（assistant/tool 往返看调用树里的其他 span）；
  //   system 完整展示（页签容器自带滚动），human 剥掉防注入包装行展示干净原文
  if (span.type === 'llm' && a.messages && a.messages.length > 0) {
    const msgs = a.messages.filter((m) => ['system', 'human', 'user'].includes(m.role))
    if (msgs.length > 0) {
      return (
        <div>
          {msgs.map((m, i) => {
            const isSystem = m.role === 'system'
            return (
              <div key={i} className="mb-2.5">
                <div className={cn('mb-1 text-[10.5px] font-semibold tracking-wide', isSystem ? 'text-[#8a7a5c]' : 'text-primary')}>
                  {isSystem ? 'SYSTEM' : 'HUMAN'}
                </div>
                <div
                  className={cn(
                    'whitespace-pre-wrap break-words rounded-lg px-3 py-2 leading-relaxed',
                    isSystem ? 'bg-secondary text-muted-foreground' : 'bg-primary/10',
                  )}
                >
                  {stripInputWrapper(m.content) || '(空)'}
                </div>
              </div>
            )
          })}
        </div>
      )
    }
  }
  // 2.工具调用：输入页签展示模型发起的 function call 参数原文
  return (
    <div>
      <div className="mb-1.5 text-[11px] text-muted-foreground">
        {span.type === 'tool' ? 'Function Call 参数' : '输入参数'}
      </div>
      <pre className="overflow-x-auto rounded-lg border bg-background p-3 font-mono text-[12px] leading-relaxed">
        {JSON.stringify(a.args ?? {}, null, 2)}
      </pre>
    </div>
  )
}

function OutputTab({ span }: { span: SpanOut }) {
  const a = span.attributes
  if (span.type === 'llm') {
    const tcs = a.tool_calls ?? []
    return (
      <div>
        <div className="mb-1.5 text-[11px] text-muted-foreground">输出正文</div>
        <div className="whitespace-pre-wrap rounded-lg border bg-background p-3 font-mono text-[12px] leading-relaxed">
          {a.output || '(无正文)'}
        </div>
        {tcs.length > 0 && (
          <>
            <div className="mb-1.5 mt-3 text-[11px] text-muted-foreground">工具调用</div>
            {tcs.map((t, i) => (
              <span key={i} className="mb-1.5 mr-1.5 inline-block rounded-md bg-primary/10 px-2 py-1 font-mono text-[11px] text-primary">
                {t.name}({JSON.stringify(t.args ?? {})})
              </span>
            ))}
          </>
        )}
      </div>
    )
  }
  return (
    <div>
      <div className="mb-1.5 text-[11px] text-muted-foreground">执行结果</div>
      <div className="whitespace-pre-wrap rounded-lg border bg-background p-3 font-mono text-[12px] leading-relaxed">
        {a.result || '(无结果)'}
      </div>
    </div>
  )
}

function ParamsTab({ span }: { span: SpanOut }) {
  const a = span.attributes
  if (span.type === 'llm') {
    // 1.优先展示后端白名单采集的真实采样参数；旧 run（参数上报前采集）退化为已有真实字段，不造假数值
    const params = a.params
    const hasParams = params && Object.keys(params).length > 0
    return (
      <div>
        <div className="mb-1.5 text-[11px] text-muted-foreground">调用参数（模型请求真实值）</div>
        <pre className="overflow-x-auto rounded-lg border bg-background p-3 font-mono text-[12px] leading-relaxed">
          {JSON.stringify(
            hasParams ? params : { model: span.model_name, finish_reason: a.finish_reason ?? undefined },
            null,
            2,
          )}
        </pre>
        {!hasParams && (
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            该 run 采集于参数上报之前，缺采样参数；新产生的 run 会带完整调用参数。
          </p>
        )}
      </div>
    )
  }
  // 2.工具调用：参数页签展示模型给的 function call args
  return (
    <div>
      <div className="mb-1.5 text-[11px] text-muted-foreground">工具参数（模型给的 args）</div>
      <pre className="overflow-x-auto rounded-lg border bg-background p-3 font-mono text-[12px] leading-relaxed">
        {JSON.stringify(a.args ?? {}, null, 2)}
      </pre>
    </div>
  )
}
