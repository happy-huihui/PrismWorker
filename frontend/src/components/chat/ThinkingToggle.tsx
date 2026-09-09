import { Brain } from 'lucide-react'

import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

interface ThinkingToggleProps {
  enabled: boolean
  onChange: (enabled: boolean) => void
  /** 当前模型是否支持思考（false 时隐藏开关或置灰） */
  supported?: boolean
  disabled?: boolean
}

export function ThinkingToggle({ enabled, onChange, supported = true, disabled }: ThinkingToggleProps) {
  if (!supported) return null

  return (
    <label
      className={cn(
        'flex h-8 cursor-pointer items-center gap-1.5 rounded-md px-2 text-xs font-normal text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground',
        disabled && 'pointer-events-none opacity-50',
      )}
    >
      <Brain className="size-3.5" />
      <span>深度思考</span>
      <Switch
        checked={enabled}
        onCheckedChange={onChange}
        disabled={disabled}
        aria-label="深度思考"
        className="ml-0.5 scale-75"
      />
    </label>
  )
}