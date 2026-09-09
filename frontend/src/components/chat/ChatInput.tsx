import { useEffect, useRef, useState } from 'react'
import { ArrowUp, Paperclip, Square } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

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
}

export function ChatInput({
  value,
  onChange,
  isRunning,
  onSend,
  onStop,
  onAttach,
  disabled,
  placeholder = '输入消息，Enter 发送，Shift+Enter 换行',
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
    <div className="flex items-end gap-1.5 rounded-xl border bg-card p-2 shadow-sm">
      <input
        ref={fileRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => pickFiles(e.target.files)}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="size-8 shrink-0 text-muted-foreground"
        onClick={() => fileRef.current?.click()}
        disabled={disabled || isRunning}
        title="上传附件"
      >
        <Paperclip className="size-4" />
      </Button>

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
        className={cn(
          'max-h-40 min-h-0 flex-1 resize-none border-0 bg-transparent px-1 py-1.5 shadow-none focus-visible:ring-0',
        )}
      />
      {isRunning ? (
        <Button
          type="button"
          variant="destructive"
          size="icon"
          className="size-8 shrink-0"
          onClick={onStop}
          title="停止"
        >
          <Square className="size-3.5" fill="currentColor" />
        </Button>
      ) : (
        <Button
          type="button"
          size="icon"
          className="size-8 shrink-0"
          onClick={submit}
          disabled={disabled || !value.trim()}
          title="发送"
        >
          <ArrowUp className="size-4" />
        </Button>
      )}
    </div>
  )
}