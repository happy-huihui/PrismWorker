/**
 * 澄清参数解析（clarificationArgs）
 *
 * 职责：把 ask_clarification 工具的参数规整成渲染层能直接用的结构。
 *
 * 为什么单独抽一个模块：
 *   1. 这段逻辑踩过一次坑 —— 最初只解析了 `options`，漏了 `fields`，
 *      结果模型给的表单（select / multi_select / textarea）一个都渲染不出来，
 *      用户看到气泡里只有一句问句、没有任何可点的东西。这类「字段漏解析」
 *      是静默失败（不报错、只是少渲染），必须能脱离 UI 单独测。
 *   2. 又踩了第二次 —— 换用 MiMo 后，模型把嵌套的 `fields` 双编码成
 *      **JSON 字符串**（实测 987 字符的 str）而不是数组，`Array.isArray`
 *      判定直接失败，表单再次被静默丢弃。因此这里统一做「字符串 → 数组」容错，
 *      两种形态都能渲染。
 *   3. 本模块**不依赖 React / 路径别名**，所以能被 node 直接跑
 *      （`node --experimental-strip-types` 配合真实抓到的参数做回归）。
 */

/** 表单字段定义（对应后端 ask_clarification 的 ClarificationFormField）。 */
export interface ClarificationField {
  name: string
  label?: string
  type?: string
  required?: boolean
  options?: string[]
  placeholder?: string
}

export interface ClarificationArgs {
  question: string
  /** 顶层单选选项（工具的「给选项」形态） */
  options: string[]
  context: string | null
  /** 五类澄清类型之一 */
  type: string | null
  /** 表单字段（工具的「一次性填表」形态，文档强烈推荐） */
  fields: ClarificationField[]
}

function pickString(v: unknown): string | null {
  return typeof v === 'string' ? v : null
}

/**
 * 把「数组或 JSON 字符串」统一还原成数组。
 *
 * 背景：模型（实测 MiMo）会把嵌套的 fields 参数双编码成字符串，
 * 形如 `"[{\"name\":\"style\",...}]"`，后端原样透传。若只判断
 * `Array.isArray`，这类参数会被静默丢掉、表单一个字段都渲染不出。
 * 解析失败一律返回空数组（保持静默降级，不抛错打断渲染）。
 */
function coerceArray(v: unknown): unknown[] {
  if (Array.isArray(v)) return v
  if (typeof v !== 'string') return []
  const trimmed = v.trim()
  // 只对疑似数组的字符串做解析，避免把普通文本当 JSON 试错
  if (!trimmed.startsWith('[')) return []
  try {
    const parsed: unknown = JSON.parse(trimmed)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function pickStringList(v: unknown): string[] {
  return coerceArray(v).filter((x): x is string => typeof x === 'string')
}

/** 规整字段数组，丢弃没有 name 的脏数据（兼容 JSON 字符串形态）。 */
export function normalizeFields(raw: unknown): ClarificationField[] {
  const out: ClarificationField[] = []
  for (const item of coerceArray(raw)) {
    if (!item || typeof item !== 'object') continue
    const f = item as Record<string, unknown>
    const name = typeof f.name === 'string' ? f.name : ''
    if (!name) continue
    const opts = pickStringList(f.options)
    out.push({
      name,
      label: pickString(f.label) ?? undefined,
      type: pickString(f.type) ?? undefined,
      required: f.required === true,
      options: opts.length > 0 ? opts : undefined,
      placeholder: pickString(f.placeholder) ?? undefined,
    })
  }
  return out
}

/** 把任意对象规整成 ClarificationArgs（结构化 args 与 args_preview 两条来源共用）。 */
export function parseShape(src: Record<string, unknown>): ClarificationArgs {
  return {
    question: pickString(src.question) ?? '',
    options: pickStringList(src.options),
    context: pickString(src.context),
    type: pickString(src.clarification_type),
    fields: normalizeFields(src.fields),
  }
}

/** 把「对象或 JSON 字符串」统一还原成对象（还原失败返回空对象）。 */
function asRecord(v: unknown): Record<string, unknown> {
  // 已经是对象直接用
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    return v as Record<string, unknown>
  }
  // 字符串形态：尝试当 JSON 解析（args_preview 就是这种来源）
  if (typeof v === 'string') {
    try {
      const parsed: unknown = JSON.parse(v)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>
      }
    } catch {
      // 非 JSON 字符串按空对象处理
    }
  }
  return {}
}

/**
 * 解析澄清参数。优先用结构化 `args`（新数据，字段完整）；
 * 老数据没有结构化 args、或结构化字段解析不出内容时，回退解析 `args_preview`。
 */
export function parseClarificationArgs(args: unknown, argsPreview: string): ClarificationArgs {
  const direct = parseShape(asRecord(args))
  if (direct.question || direct.fields.length > 0 || direct.options.length > 0) return direct
  const fallback = parseShape(asRecord(argsPreview))
  // 兜底也没内容时，保留 direct 以免丢掉已有字段
  return fallback.question || fallback.fields.length > 0 || fallback.options.length > 0
    ? fallback
    : direct
}

/**
 * 把表单答案拼成人类可读的多行文本。
 *
 * 格式（`标签：值`，一行一项，跳过空值）：
 *     联赛类型：英超 Premier League
 *     网站板块：积分榜、赛程/比分
 */
export function composeAnswer(
  fields: ClarificationField[],
  answers: Record<string, string>,
): string {
  const lines: string[] = []
  for (const f of fields) {
    const value = (answers[f.name] ?? '').trim()
    if (!value) continue
    lines.push(`${f.label || f.name}：${value}`)
  }
  return lines.join('\n')
}
