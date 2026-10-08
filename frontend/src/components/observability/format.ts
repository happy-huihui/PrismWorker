/**
 * 观测中台格式化（components/observability/format）
 *
 * 职责：token / 成本 / 耗时 / 时间的紧凑展示 + span 展示名兜底清洗，
 *       精度口径与 doc/observability-template 对齐（列表 4 位小数、汇总 3 位、极小值 5 位）。
 */

/** token 数人性化：K/M 缩写，未知给 — */
export function formatTokens(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n) || n < 0) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

/** 成本（美元）：汇总口径——<0.01 保留 5 位小数，其余 3 位（模板 fmtCost 同款） */
export function formatCost(cost: number | null | undefined): string {
  if (cost == null || !Number.isFinite(cost) || cost <= 0) return '—'
  if (cost < 0.01) return `$${cost.toFixed(5)}`
  return `$${cost.toFixed(3)}`
}

/** 成本（美元）：列表行口径——固定 4 位小数（模板运行记录/Top5 行同款） */
export function formatCostList(cost: number | null | undefined): string {
  if (cost == null || !Number.isFinite(cost) || cost <= 0) return '—'
  return `$${cost.toFixed(4)}`
}

/** 耗时（毫秒）：<1s 用 ms，否则秒（两位内） */
export function formatMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

/**
 * 相对时间（模板 timeAgo 同款）：刚刚 / N分钟前 / N小时前 / N天前。
 * 与 lib/format 的区别：不带 date-fns 的空格与「约」，且不因超过 7 天回退绝对时间——
 * 观测列表按模板始终展示相对时间，宽度稳定不撑爆表格。
 */
export function formatAgo(timestampSeconds: number): string {
  const minutes = Math.floor((Date.now() / 1000 - timestampSeconds) / 60)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  if (minutes < 1440) return `${Math.floor(minutes / 60)}小时前`
  return `${Math.floor(minutes / 1440)}天前`
}

/** 短时钟 HH:MM:SS（日志流时间列用，模板 log-time 同款窄列） */
export function formatClock(timestampSeconds: number): string {
  return new Date(timestampSeconds * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}

/**
 * 剥掉输入净化中间件的防注入包装行（展示层专用，库里仍存原文）。
 * 匹配 `--- BEGIN USER INPUT ---` / `--- END USER INPUT ---` 及 `[BEGIN USER INPUT]` 中和变体。
 */
export function stripInputWrapper(text: string): string {
  return text
    .split('\n')
    .filter((line) => !/^\s*[-[]*\s*(BEGIN|END) USER INPUT\s*[-\]]*\s*$/.test(line))
    .join('\n')
    .trim()
}

/**
 * span 展示名兜底清洗。
 *
 * 为什么：后端已修 `_llm_span_name` 的 repr 碎片问题，但库里历史 run 的 span 名
 * 仍可能是 `llm.metadata={'lc_versions': ...}` 这类垃圾——展示层再兜一道：
 * 名字含 repr 特征符（= { } < >）或超长时，llm span 优先用 `llm.{model_name}`，
 * 其余类型截断防撑爆布局。
 */
export function displaySpanName(
  name: string,
  type: string,
  modelName?: string | null,
): string {
  // 1.干净名字直接用（模型名不会出现 = { } < > 等符号）
  if (name && !/[={}<>{}[\]\n]/.test(name) && name.length <= 64) return name
  // 2.llm span：库里有干净 model_name → 拼回标准名
  if (type === 'llm' && modelName && !/[={}<>{}[\]\n]/.test(modelName)) {
    return `llm.${modelName}`
  }
  // 3.最后兜底：截断 + 省略号，保证不破坏布局
  return name.length > 48 ? `${name.slice(0, 48)}…` : name || '(unnamed)'
}
