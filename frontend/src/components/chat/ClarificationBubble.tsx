import { useState } from 'react'

import { cn } from '@/lib/utils'

import { type ToolCallItem } from '@/core/runs/useRunStream'

import {
  type ClarificationField,
  composeAnswer,
  parseClarificationArgs,
} from './clarificationArgs'

/**
 * 澄清气泡（ClarificationBubble）
 *
 * 职责：把 ask_clarification 工具调用的 args 渲染为可见、可交互的对话气泡。
 *
 * 为什么需要它：ask_clarification 的 content 只是占位字符串
 *      （"Clarification request processed by middleware"），真信息全在 args 里。
 *      此前只在 ThinkingChain 的 ToolRow 出「请求澄清」一行，用户看不到任何问句，
 *      体验就是「停在这里，什么输出也没有」。
 *
 * 工具支持**三种**提问形态（见 ask_clarification 工具文档），必须都能渲染：
 *      1. question  —— 纯文本提问（用户打字回复）
 *      2. options   —— 单选选项（点击即填入输入框）
 *      3. fields    —— 表单（工具文档**强烈推荐**，实测模型也确实主要用它）
 *
 *      ⚠️ 曾经的缺陷：只实现了 options，没实现 fields。而模型按工具文档的
 *      建议大量使用 fields（实测一次 4 个字段：select / multi_select / textarea），
 *      于是气泡里只剩一句问句、**一个可点的东西都没有**，用户自然要问
 *      「选项在哪」。本版补齐 fields，并把 footer 文案改成按实际情况显示。
 *      解析逻辑抽到 `clarificationArgs.ts`（无 React 依赖）以便脱离 UI 做回归。
 *
 * 提交方式：表单填好后点「填入回复」→ 把答案拼成人类可读的多行文本塞进下方
 *      输入框（不直接发送）。理由：用户还能改、能补充，避免一键发错还得重来。
 */

interface ClarificationBubbleProps {
  tool: ToolCallItem
  /** 把拼好的回复文本塞进下方输入框 */
  onFillInput?: (text: string) => void
  /** 没有 onFillInput 时退化为直接发送 */
  onSendOption?: (text: string) => void
}

const TYPE_LABEL: Record<string, string> = {
  missing_info: '需要更多信息',
  ambiguous_requirement: '需求有歧义',
  approach_choice: '需要你拍板方案',
  risk_confirmation: '风险确认',
  suggestion: '提个建议',
}

function typeLabel(t: string | null): string {
  if (!t) return '向你确认'
  return TYPE_LABEL[t] ?? '向你确认'
}

/** 选项 chip：选中态高亮，点击切换。select / multi_select 共用外观。 */
function OptionChip({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'rounded-full border px-3 py-1 text-left text-xs font-medium transition-all',
        active
          ? 'border-amber-500 bg-amber-400/90 text-amber-950 shadow-sm dark:border-amber-400 dark:bg-amber-500/80 dark:text-amber-950'
          : 'border-amber-400/60 bg-amber-100/50 text-amber-900 hover:border-amber-500 hover:bg-amber-200/70 dark:border-amber-600/60 dark:bg-amber-900/30 dark:text-amber-100 dark:hover:bg-amber-800/50',
      )}
    >
      {label}
    </button>
  )
}

/** 文本类输入控件（text / textarea 共用样式）。 */
function TextControl({
  multiline,
  value,
  placeholder,
  onChange,
}: {
  multiline: boolean
  value: string
  placeholder?: string
  onChange: (v: string) => void
}) {
  const cls =
    'w-full rounded-md border border-amber-300/60 bg-card px-2.5 py-1.5 text-xs outline-none focus:border-amber-500 focus:ring-1 focus:ring-amber-400/40 dark:border-amber-700/50'
  if (multiline) {
    return (
      <textarea
        value={value}
        placeholder={placeholder}
        rows={2}
        onChange={(e) => onChange(e.target.value)}
        className={cn(cls, 'resize-y')}
      />
    )
  }
  return (
    <input
      type="text"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className={cls}
    />
  )
}

export function ClarificationBubble({ tool, onFillInput, onSendOption }: ClarificationBubbleProps) {
  const { question, options, context, type, fields } = parseClarificationArgs(
    tool.args,
    tool.args_preview,
  )

  // 表单答案分三类存：单选/日期/数字、多选、自由文本
  const [picked, setPicked] = useState<Record<string, string>>({})
  const [multiPicked, setMultiPicked] = useState<Record<string, string[]>>({})
  const [typed, setTyped] = useState<Record<string, string>>({})

  /** 某字段当前答案（统一成字符串，供判空与拼接）。 */
  const answerOf = (f: ClarificationField): string => {
    if (f.type === 'multi_select') return (multiPicked[f.name] ?? []).join('、')
    if (f.type === 'select' || f.type === 'checkbox' || f.type === 'date' || f.type === 'number') {
      return picked[f.name] ?? ''
    }
    return typed[f.name] ?? ''
  }

  /** 汇总成 { 字段名: 答案 } 交给纯函数拼接（保证与回归测试用同一份逻辑）。 */
  const collectAnswers = (): Record<string, string> => {
    const out: Record<string, string> = {}
    for (const f of fields) out[f.name] = answerOf(f)
    return out
  }

  const missingRequired = fields
    .filter((f) => f.required && !answerOf(f).trim())
    .map((f) => f.label || f.name)

  const emit = (text: string) => {
    if (!text.trim()) return
    if (onFillInput) onFillInput(text)
    else onSendOption?.(text)
  }

  const hasFields = fields.length > 0
  const hasOptions = options.length > 0

  // 完全无内容可渲染时的兜底（不该发生，但别让用户看到空气泡）
  if (!question && !hasFields && !hasOptions) {
    return (
      <div className="flex gap-2 rounded-xl border border-dashed bg-muted/30 px-4 py-3 text-sm text-muted-foreground">
        <span aria-hidden>❓</span>
        <span>模型请求澄清，但问题内容缺失（args 不可用）</span>
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-xl border border-amber-300/60 bg-amber-50/40 shadow-sm dark:border-amber-700/50 dark:bg-amber-950/20">
      {/* 1.头部：❓ + 类型标签 + 工具名 */}
      <div className="flex items-center gap-2 border-b border-amber-300/40 bg-amber-100/40 px-4 py-2 text-amber-900 dark:border-amber-700/40 dark:bg-amber-900/30 dark:text-amber-100">
        <span className="text-base" aria-hidden>
          ❓
        </span>
        <span className="text-sm font-medium">{typeLabel(type)}</span>
        <span className="ml-auto rounded-full bg-amber-200/70 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-amber-900 uppercase dark:bg-amber-900/60 dark:text-amber-200">
          {tool.tool}
        </span>
      </div>

      {/* 2.正文：context（背景）+ question（问句） */}
      {(context || question) && (
        <div className="space-y-2 px-4 py-3 text-sm leading-relaxed text-foreground/90">
          {context && <p className="text-xs text-muted-foreground">{context}</p>}
          {question && <p className="whitespace-pre-wrap">{question}</p>}
        </div>
      )}

      {/* 3.表单（fields）——工具文档推荐的主用法，按类型分别渲染 */}
      {hasFields && (
        <div className="space-y-3 border-t border-amber-300/30 px-4 py-3">
          {fields.map((f) => {
            const label = f.label || f.name
            const ft = f.type || 'text'
            const isChoice = ft === 'select' || ft === 'multi_select'
            return (
              <div key={f.name} className="space-y-1.5">
                <div className="flex items-center gap-1.5 text-xs font-medium text-amber-900 dark:text-amber-100">
                  <span>{label}</span>
                  {f.required && <span className="text-amber-600 dark:text-amber-300">*</span>}
                  {ft === 'multi_select' && (
                    <span className="font-normal text-amber-900/60 dark:text-amber-200/60">
                      （可多选）
                    </span>
                  )}
                </div>

                {isChoice ? (
                  <div className="flex flex-wrap gap-1.5">
                    {(f.options ?? []).length > 0 ? (
                      (f.options ?? []).map((opt) => {
                        const active =
                          ft === 'multi_select'
                            ? (multiPicked[f.name] ?? []).includes(opt)
                            : picked[f.name] === opt
                        return (
                          <OptionChip
                            key={`${f.name}-${opt}`}
                            label={opt}
                            active={active}
                            onClick={() => {
                              if (ft === 'multi_select') {
                                setMultiPicked((prev) => {
                                  const cur = prev[f.name] ?? []
                                  return {
                                    ...prev,
                                    [f.name]: cur.includes(opt)
                                      ? cur.filter((x) => x !== opt)
                                      : [...cur, opt],
                                  }
                                })
                              } else {
                                setPicked((prev) => ({ ...prev, [f.name]: opt }))
                              }
                            }}
                          />
                        )
                      })
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        （模型未给出选项，请在下方输入框说明）
                      </span>
                    )}
                  </div>
                ) : ft === 'checkbox' ? (
                  <label className="flex cursor-pointer items-center gap-2 text-xs text-foreground/80">
                    <input
                      type="checkbox"
                      checked={picked[f.name] === '是'}
                      onChange={(e) =>
                        setPicked((prev) => ({
                          ...prev,
                          [f.name]: e.target.checked ? '是' : '否',
                        }))
                      }
                      className="size-3.5 accent-amber-600"
                    />
                    <span>{f.placeholder || '勾选表示同意'}</span>
                  </label>
                ) : ft === 'date' || ft === 'number' ? (
                  <input
                    type={ft}
                    value={picked[f.name] ?? ''}
                    placeholder={f.placeholder}
                    onChange={(e) => setPicked((prev) => ({ ...prev, [f.name]: e.target.value }))}
                    className="w-full rounded-md border border-amber-300/60 bg-card px-2.5 py-1.5 text-xs outline-none focus:border-amber-500 focus:ring-1 focus:ring-amber-400/40 dark:border-amber-700/50"
                  />
                ) : (
                  // text / textarea / 未知类型 → 文本输入兜底
                  <TextControl
                    multiline={ft === 'textarea'}
                    value={typed[f.name] ?? ''}
                    placeholder={f.placeholder}
                    onChange={(v) => setTyped((prev) => ({ ...prev, [f.name]: v }))}
                  />
                )}
              </div>
            )
          })}

          {/* 表单提交：拼成文本填入输入框（用户可改后再发） */}
          <div className="flex items-center gap-2 pt-0.5">
            <button
              type="button"
              onClick={() => emit(composeAnswer(fields, collectAnswers()))}
              disabled={missingRequired.length > 0}
              className={cn(
                'rounded-lg border px-3 py-1.5 text-xs font-medium transition-all',
                missingRequired.length > 0
                  ? 'cursor-not-allowed border-muted-foreground/20 bg-muted text-muted-foreground/60'
                  : 'border-amber-500 bg-amber-400/90 text-amber-950 hover:bg-amber-300 hover:shadow-sm dark:border-amber-400 dark:bg-amber-500/80',
              )}
            >
              填入回复
            </button>
            {missingRequired.length > 0 && (
              <span className="text-[11px] text-muted-foreground">
                还需选择：{missingRequired.join('、')}
              </span>
            )}
          </div>
        </div>
      )}

      {/* 4.顶层选项（options）——工具的单选形态，点击直接填入输入框 */}
      {!hasFields && hasOptions && (
        <div className="flex flex-wrap gap-1.5 border-t border-amber-300/30 px-4 py-3">
          {options.map((opt) => (
            <OptionChip key={opt} label={opt} active={false} onClick={() => emit(opt)} />
          ))}
        </div>
      )}

      {/* 5.底部提示：按实际可交互内容显示，不再一律说「点击选项」 */}
      <div className="border-t border-amber-300/30 bg-amber-50/30 px-4 py-1.5 text-[11px] text-amber-900/70 dark:border-amber-700/30 dark:bg-amber-950/20 dark:text-amber-200/70">
        {hasFields
          ? '选好后点「填入回复」，文本会进入下方输入框，确认无误再发送'
          : hasOptions
            ? '点击选项会填入下方输入框，请确认后发送'
            : '请在下方输入框回复'}
      </div>
    </div>
  )
}
