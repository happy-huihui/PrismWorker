/**
 * 回归验证（前端澄清表单解析）：fields / options 被模型输出成 JSON 字符串时不丢控件。
 *
 * 背景：
 *   `ask_clarification` 的 `fields` 是嵌套数组。实测 MiMo 会把它**双编码成
 *   JSON 字符串**（987 字符的 str）而不是数组，后端原样透传；
 *   而 `clarificationArgs.ts` 原先只判断 `Array.isArray`，于是
 *   `normalizeFields` 返回空数组、`parseClarificationArgs` 又因 question
 *   非空提前 return、走不到 args_preview 兜底 —— 结果是**静默失败**：
 *   气泡里只剩一句问句，select / multi_select / textarea 一个控件都不渲染，
 *   用户看起来就像「根本没让我澄清」。DeepSeek 输出的是真数组，所以换模型才暴露。
 *
 * 与 scripts/verify-round-demotion.mjs 的区别：
 *   那份把逻辑逐字复刻成 JS；这份直接 **import 真实模块**（`--experimental-strip-types`
 *   剥离类型后运行），不存在「复刻副本与实现漂移」的问题。
 *
 * 用法：
 *   cd frontend && node --experimental-strip-types scripts/verify-clarification-args.mts
 */

import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, resolve } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
// Windows 下动态 import 必须给 file:// URL，直接传 E:\... 会报 ERR_UNSUPPORTED_ESM_URL_SCHEME
const MODULE_URL = pathToFileURL(resolve(HERE, '../src/components/chat/clarificationArgs.ts')).href

const { parseClarificationArgs, normalizeFields } = (await import(MODULE_URL)) as {
  parseClarificationArgs: (args: unknown, preview: string) => {
    question: string
    type: string | null
    options: string[]
    fields: Array<{ name: string; type?: string; required?: boolean; options?: string[] }>
  }
  normalizeFields: (raw: unknown) => Array<{ name: string }>
}

let failed = 0
function check(label: string, actual: unknown, expected: unknown) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failed += 1
  console.log(`${ok ? '  ✓' : '  ✗'} ${label}`)
  if (!ok) {
    console.log(`      实际=${JSON.stringify(actual)}`)
    console.log(`      期望=${JSON.stringify(expected)}`)
  }
}

// ── 真实抓到的 payload（2026-09-25，MiMo mimo-v2.6-pro，输入「给我做一个关于足球联赛的炫酷网站」）
// 后端原样透传给前端时，fields 是 JSON 字符串而非数组；此处按原样复现该形态。
const CAPTURED_FIELDS = [
  {
    name: 'league_type',
    label: '联赛设定（选哪种？）',
    type: 'select',
    required: true,
    options: [
      '虚构概念联赛（我来设计联赛品牌、球队、球星，最有创作空间）',
      '真实联赛致敬站（如英超/西甲/意甲/中超，用真实球队与氛围）',
      '足球资讯数据站（积分榜/赛程/转会新闻为主）',
      '电竞/游戏联赛（FIFA/EA FC 风格的电竞赛事站）',
    ],
  },
  {
    name: 'league_name',
    label: '联赛名称（虚构联赛可留空，我来起名；真实联赛请写名称）',
    type: 'text',
    required: false,
    placeholder: '例如：英超 / 中超 / 或留空让我设计',
  },
  { name: 'language', label: '网站语言', type: 'select', required: true, options: ['中文', '英文', '中英双语'] },
  {
    name: 'style',
    label: '视觉风格调性',
    type: 'select',
    required: true,
    options: [
      '暗黑霓虹电竞风（黑底+荧光绿/蓝，最有「炫酷」感）',
      '绿茵经典运动风（草皮绿+白+炭黑，像 Adidas/耐克广告）',
      '炫彩渐变未来风（紫蓝橙渐变、玻璃拟态、大字报）',
      '极简高级杂志风（大留白+巨幅排版+黑白摄影感）',
    ],
  },
  {
    name: 'pages',
    label: '需要哪些板块（可多选）',
    type: 'multi_select',
    required: true,
    options: [
      '首屏 Hero + 联赛口号',
      '积分榜/排名',
      '赛程与比分',
      '球队与球星卡',
      '新闻资讯/头条',
      '精彩进球/视频集锦区',
      '关于联赛/历史数据',
    ],
  },
  {
    name: 'extra',
    label: '其他补充要求（可留空）',
    type: 'textarea',
    required: false,
    placeholder: '例如：要带动效、要适配手机、配色避开某颜色、参考某个网站…',
  },
]

const MIMO_ARGS = {
  question: '「足球联赛的炫酷网站」有几种做法，帮我定一下方向，我一次做完：',
  clarification_type: 'ambiguous_requirement',
  context: '避免做完才发现方向不对',
  // ⚠️ 关键：MiMo 给的是字符串
  fields: JSON.stringify(CAPTURED_FIELDS),
}

console.log('[1] MiMo 形态：fields 是 JSON 字符串（本次修复的目标）')
const parsed = parseClarificationArgs(MIMO_ARGS, '')
check('question 保留', parsed.question.length > 0, true)
check('type 保留', parsed.type, 'ambiguous_requirement')
check('fields 解析出 6 项', parsed.fields.length, 6)
check('字段名与顺序正确', parsed.fields.map((f) => f.name), [
  'league_type',
  'league_name',
  'language',
  'style',
  'pages',
  'extra',
])
check('select 的 options 没丢', (parsed.fields.find((f) => f.name === 'style')?.options ?? []).length, 4)
check('required 标记保留', parsed.fields.find((f) => f.name === 'style')?.required, true)
check('multi_select 类型保留', parsed.fields.find((f) => f.name === 'pages')?.type, 'multi_select')
check('textarea 类型保留', parsed.fields.find((f) => f.name === 'extra')?.type, 'textarea')

console.log('\n[2] DeepSeek 形态：fields 本来就是数组（不得回归）')
check(
  'fields 仍 6 项',
  parseClarificationArgs({ ...MIMO_ARGS, fields: CAPTURED_FIELDS }, '').fields.length,
  6,
)

console.log('\n[3] 顶层 options 的两种形态')
check('options 是数组', parseClarificationArgs({ question: 'Q', options: ['A', 'B'] }, '').options, ['A', 'B'])
check('options 是 JSON 字符串', parseClarificationArgs({ question: 'Q', options: '["A","B"]' }, '').options, ['A', 'B'])

console.log('\n[4] 脏数据：不能崩，也不能误判')
check('fields 是坏 JSON', normalizeFields('[{憋坏了').length, 0)
check('fields 是普通文本', normalizeFields('随便一句中文').length, 0)
check('fields 是 null', normalizeFields(null).length, 0)
check('fields 是数字', normalizeFields(42).length, 0)
check('数组里混脏项只留合法的', normalizeFields([{ name: 'ok' }, null, { noName: 1 }, 'x']).length, 1)

console.log('\n[5] 老数据兜底：只有 args_preview 字符串')
const preview = JSON.stringify({ question: '老问题', fields: CAPTURED_FIELDS.slice(0, 2) })
check('回退解析 args_preview', parseClarificationArgs({}, preview).fields.length, 2)
check('整个 args 是 JSON 字符串也能解析', parseClarificationArgs(preview, '').fields.length, 2)

console.log(`\n===== ${failed === 0 ? '全部通过' : `${failed} 项失败`} =====`)
process.exit(failed === 0 ? 0 : 1)
