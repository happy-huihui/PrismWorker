import type { SpanOut } from '@/core/observability/types'

/**
 * span 树组装（components/observability/tree）
 *
 * 职责：把扁平 span 列表按 parent_span_id 组装成嵌套树，供调用树 / 瀑布图共用，
 *      避免两处各写一份组装逻辑。
 */

export interface SpanNode {
  span: SpanOut
  children: SpanNode[]
}

/** 扁平 span → 嵌套树（parent_span_id 挂父；父缺失/无父归根） */
export function buildSpanTree(spans: SpanOut[]): SpanNode[] {
  const byId = new Map<string, SpanNode>()
  spans.forEach((s) => byId.set(s.span_id, { span: s, children: [] }))
  const roots: SpanNode[] = []
  byId.forEach((node) => {
    const parent = node.span.parent_span_id ? byId.get(node.span.parent_span_id) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  })
  return roots
}

/** 树先序遍历成带深度的扁平列表（瀑布图按此渲染，保持层级缩进） */
export function flattenSpanTree(roots: SpanNode[]): Array<{ span: SpanOut; depth: number }> {
  const out: Array<{ span: SpanOut; depth: number }> = []
  const walk = (nodes: SpanNode[], depth: number) => {
    nodes.forEach((n) => {
      out.push({ span: n.span, depth })
      walk(n.children, depth + 1)
    })
  }
  walk(roots, 0)
  return out
}
