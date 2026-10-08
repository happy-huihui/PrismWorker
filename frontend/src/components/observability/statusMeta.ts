/**
 * 观测中台状态元信息（components/observability/statusMeta）
 *
 * 职责：把 run 状态 / span 类型映射成「中文文案 + badge 变体」，列表与详情共用，
 *      避免散落各处各写一份映射。
 */

// badge 变体（与 components/ui/badge.tsx 的 variant 对齐）
export type StatusVariant = 'default' | 'secondary' | 'destructive' | 'outline' | 'success'

export interface StatusMeta {
  label: string
  variant: StatusVariant
}

/** run 状态 → 文案 + 颜色 */
export const RUN_STATUS_META: Record<string, StatusMeta> = {
  pending: { label: '等待中', variant: 'secondary' },
  running: { label: '运行中', variant: 'default' },
  finished: { label: '已完成', variant: 'success' },
  cancelled: { label: '已取消', variant: 'outline' },
  error: { label: '失败', variant: 'destructive' },
}

/** span 类型 → 文案 + 颜色 */
export const SPAN_TYPE_META: Record<string, StatusMeta> = {
  llm: { label: '模型', variant: 'default' },
  tool: { label: '工具', variant: 'secondary' },
  retrieval: { label: '检索', variant: 'outline' },
  agent: { label: '子代理', variant: 'outline' },
}

export function runStatusMeta(status: string): StatusMeta {
  return RUN_STATUS_META[status] ?? { label: status, variant: 'outline' }
}

export function spanTypeMeta(type: string): StatusMeta {
  return SPAN_TYPE_META[type] ?? { label: type, variant: 'outline' }
}
