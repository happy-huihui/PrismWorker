import { ChatInput } from '@/components/chat/ChatInput'
import { ThinkingToggle } from '@/components/chat/ThinkingToggle'

/**
 * 输入区（Composer）
 *
 * 职责：对话底部输入区，对齐模板 .composer/.inbox——820px 居中卡片，
 *      卡片内工具行 = 思考药丸（toolbar 插槽传入 ChatInput）。
 *
 * 注意：这里**不再有模型选择器**。模型由后端「动态路由」按输入自动决定
 * （默认 mimo-v2.6-pro，命中深度分析/写代码等关键词时也保持 pro），
 * 决策结果通过 run_meta 下发，在思考链里如实展示。用户仍然保留
 * 「深度思考」开关——它是显式的用户意图，与自动路由互补。
 */
interface ComposerProps {
  value: string
  onChange: (value: string) => void
  onSend: (text: string) => void
  onStop?: () => void
  onAttach?: (files: File[]) => void
  isRunning?: boolean
  disabled?: boolean
  placeholder?: string
  // 运行参数
  thinking: boolean
  onThinkingChange: (enabled: boolean) => void
  /** 当前模型是否支持深度思考 */
  thinkingSupported?: boolean
}

export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  onAttach,
  isRunning,
  disabled,
  placeholder,
  thinking,
  onThinkingChange,
  thinkingSupported,
}: ComposerProps) {
  // 运行中禁用参数修改（避免中途切思考）
  const controlsDisabled = isRunning || disabled

  return (
    <div className="mx-auto w-full max-w-[820px]">
      <ChatInput
        value={value}
        onChange={onChange}
        onSend={onSend}
        onStop={onStop}
        onAttach={onAttach}
        isRunning={isRunning}
        disabled={disabled}
        placeholder={placeholder}
        toolbar={
          <ThinkingToggle
            enabled={thinking}
            onChange={onThinkingChange}
            supported={thinkingSupported}
            disabled={controlsDisabled}
          />
        }
      />
    </div>
  )
}
