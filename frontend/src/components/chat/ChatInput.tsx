import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowUp, Paperclip, Square } from '@/components/icons'

import { Textarea } from '@/components/ui/textarea'

/**
 * 输入卡片（ChatInput）
 *
 * 结构对齐模板 .inbox：一张卡片内 = 上文本域 + 下工具行；
 * 工具行 = [toolbar 插槽（模型/思考药丸由 Composer 传入）] + 回形针 + 发送键。
 * 发送键 32px 主色圆角方块（模板 .send）；运行中变为「停止」。
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
}: ChatInputProps) {
  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [isComposing, setIsComposing] = useState(false)

  const pickFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return
    const list = Array.from(files)
    if (fileRef.current) fileRef.current.value = ''
    onAttach?.(list)
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
    <div className="rounded-[14px] border border-input bg-card p-3 shadow-sm transition-shadow focus-within:shadow-md focus-within:ring-2 focus-within:ring-ring/30">
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
        onChange={(e) => onChange(e.target.value)}
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
          if (e.key === 'Enter' && !e.shiftKey && !isComposing) {
            e.preventDefault()
            submit()
          }
        }}
        placeholder={placeholder}
        disabled={disabled}
        className="max-h-40 min-h-0 resize-none border-0 bg-transparent px-1 py-0.5 text-[15px] shadow-none focus-visible:ring-0"
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
