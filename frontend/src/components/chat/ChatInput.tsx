import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ArrowUp, Paperclip, Square } from '@/components/icons'
import { SkillSlashMenu } from '@/components/chat/SkillSlashMenu'
import { type SkillOut } from '@/core/skills'
import { cn } from '@/lib/utils'

import { Textarea } from '@/components/ui/textarea'

/**
 * 输入卡片（ChatInput）
 *
 * 结构对齐模板 .inbox：一张卡片内 = 上文本域 + 下工具行；
 * 工具行 = [toolbar 插槽（模型/思考药丸由 Composer 传入）] + 回形针 + 发送键。
 * 发送键 32px 主色圆角方块（模板 .send）；运行中变为「停止」。
 * 斜杠技能：输入以 / 开头时在卡片上方弹出技能候选（≤6 项可滚动），
 *   ↑↓ 高亮、Enter/点击选中（回填 /技能名 ）、Esc 关闭、无匹配即关。
 */
interface ChatInputProps {
  value: string
  onChange: (value: string) => void
  /** 运行中：禁用发送，主按钮变为「停止」 */
  isRunning?: boolean
  onSend?: (text: string) => void
  onStop?: () => void
  /** 选中/粘贴文件时回调（文件上传由上层处理） */
  onAttach?: (files: File[]) => void
  disabled?: boolean
  placeholder?: string
  /** 底部工具行左侧插槽（模型选择/思考开关等药丸） */
  toolbar?: ReactNode
  /** 技能清单（含 blocked 标记；未传则不启用斜杠技能浮层） */
  skills?: SkillOut[]
  /** 主页 hero 模式：卡片与文本域更大更松弛（仅首页首问输入用） */
  hero?: boolean
}

export function ChatInput({
  value,
  onChange,
  isRunning,
  onSend,
  onStop,
  onAttach,
  disabled,
  placeholder = '追加需求或提问…',
  toolbar,
  skills,
  hero = false,
}: ChatInputProps) {
  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [isComposing, setIsComposing] = useState(false)
  // 斜杠浮层：高亮索引 + Esc 手动关闭标记
  const [activeIndex, setActiveIndex] = useState(0)
  const [dismissed, setDismissed] = useState(false)

  const pickFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return
    const list = Array.from(files)
    if (fileRef.current) fileRef.current.value = ''
    onAttach?.(list)
  }

  // 统一入口：任何输入变化都解除 Esc 关闭标记
  const handleChange = (next: string) => {
    setDismissed(false)
    onChange(next)
  }

  // ── 斜杠技能候选 ──────────────────────────────────────────────
  // 触发条件：首字符是 / 且 / 之后还没有空格（选中回填带尾空格，自动脱离触发态）
  const slashQuery = value.startsWith('/') ? value.slice(1) : null
  const queryHasSpace = slashQuery !== null && /\s/.test(slashQuery)
  const candidates = useMemo(() => {
    if (slashQuery === null || queryHasSpace) return []
    const q = slashQuery.toLowerCase()
    const list = (skills ?? []).filter(
      (s) => !s.blocked && (q === '' || s.name.toLowerCase().includes(q)),
    )
    // 公共在前、私有在后（组内按名排序），与浮层的分组展示保持同一顺序
    return list.sort((a, b) =>
      a.source === b.source ? a.name.localeCompare(b.name) : a.source === 'public' ? -1 : 1,
    )
  }, [slashQuery, queryHasSpace, skills])
  // 开启条件：处于 / 触发态、未被 Esc 关闭、且有匹配项（无匹配即关）
  const menuOpen = slashQuery !== null && !queryHasSpace && !dismissed && candidates.length > 0

  // 查询词变化时高亮归零
  useEffect(() => {
    setActiveIndex(0)
  }, [slashQuery])

  // 选中技能：回填 /技能名 + 尾空格（浮层因空格规则自动关闭）
  const selectSkill = (name: string) => {
    handleChange(`/${name} `)
    taRef.current?.focus()
  }

  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`
  }, [value])

  const submit = () => {
    const text = value.trim()
    if (!text || isComposing || isRunning || disabled) return
    onSend?.(text)
  }

  return (
    <div
      className={cn(
        'relative rounded-[14px] border border-input bg-card shadow-sm transition-shadow focus-within:shadow-md focus-within:ring-2 focus-within:ring-ring/30',
        hero ? 'p-4' : 'p-3',
      )}
    >
      {/* 0.斜杠技能浮层（绝对定位于卡片上方） */}
      {menuOpen && (
        <SkillSlashMenu
          skills={candidates}
          activeIndex={activeIndex}
          onHover={setActiveIndex}
          onSelect={selectSkill}
        />
      )}
      {/* 1.文本域：无边框透明底，placeholder 15px（模板 .ph） */}
      <input
        ref={fileRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => pickFiles(e.target.files)}
      />
      <Textarea
        ref={taRef}
        rows={1}
        value={value}
        onChange={(e) => handleChange(e.target.value)}
        onCompositionStart={() => setIsComposing(true)}
        onCompositionEnd={() => setIsComposing(false)}
        onPaste={(e) => {
          const files = e.clipboardData.files
          if (files.length > 0) {
            e.preventDefault()
            pickFiles(files)
          }
        }}
        onKeyDown={(e) => {
          // 浮层打开时优先消费导航键（Enter=选中而非发送）
          if (menuOpen) {
            if (e.key === 'ArrowDown') {
              e.preventDefault()
              setActiveIndex((i) => (i + 1) % candidates.length)
              return
            }
            if (e.key === 'ArrowUp') {
              e.preventDefault()
              setActiveIndex((i) => (i - 1 + candidates.length) % candidates.length)
              return
            }
            if (e.key === 'Enter' && !e.shiftKey && !isComposing) {
              e.preventDefault()
              selectSkill(candidates[activeIndex]?.name ?? '')
              return
            }
            if (e.key === 'Escape') {
              e.preventDefault()
              setDismissed(true)
              return
            }
          }
          if (e.key === 'Enter' && !e.shiftKey && !isComposing) {
            e.preventDefault()
            submit()
          }
        }}
        placeholder={placeholder}
        disabled={disabled}
        className={cn(
          'max-h-40 resize-none border-0 bg-transparent px-1 py-0.5 text-[15px] shadow-none focus-visible:ring-0',
          // hero 模式：文本域约两行起步（扁平自然，不做成大空腔），自动撑高逻辑不变
          hero ? 'min-h-[56px]' : 'min-h-0',
        )}
      />

      {/* 2.工具行：药丸插槽 + 回形针 + 发送/停止（模板 .inbox .row） */}
      <div className="mt-2.5 flex items-center gap-2.5">
        {toolbar}
        <div className="ml-auto flex items-center gap-1.5">
          {onAttach && (
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={disabled || isRunning}
              title="上传附件"
              aria-label="上传附件"
              className="grid size-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
            >
              <Paperclip className="size-4" />
            </button>
          )}
          {isRunning ? (
            <button
              type="button"
              onClick={onStop}
              title="停止"
              aria-label="停止"
              className="grid size-8 place-items-center rounded-[9px] bg-destructive text-destructive-foreground transition-opacity hover:opacity-90"
            >
              <Square className="size-3.5" fill="currentColor" />
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={disabled || !value.trim()}
              title="发送"
              aria-label="发送"
              className="grid size-8 place-items-center rounded-[9px] bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:pointer-events-none disabled:opacity-40"
            >
              <ArrowUp className="size-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
