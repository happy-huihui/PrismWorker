import { LaptopIcon, MoonIcon, SunIcon } from 'lucide-react'

import { useTheme } from '@/app/ThemeProvider'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

export function ThemeToggle() {
  const { theme, resolved, setTheme } = useTheme()

  const next = theme === 'light' ? 'dark' : theme === 'dark' ? 'system' : 'light'

  const Icon = resolved === 'dark' ? MoonIcon : SunIcon

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-9"
          aria-label={`切换主题（当前：${theme}）`}
          onClick={() => setTheme(next)}
        >
          <Icon className="size-4" />
        </Button>
      </TooltipTrigger>
      <TooltipContent side="right">
        {theme === 'system' ? (
          <span className="inline-flex items-center gap-1">
            <LaptopIcon className="size-3" /> 跟随系统
          </span>
        ) : resolved === 'dark' ? (
          <span>暗色模式</span>
        ) : (
          <span>亮色模式</span>
        )}
      </TooltipContent>
    </Tooltip>
  )
}