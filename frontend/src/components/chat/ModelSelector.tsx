import { useState } from 'react'
import { Check, ChevronsUpDown, RefreshCw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { useModels } from '@/core/models'
import { cn } from '@/lib/utils'

interface ModelSelectorProps {
  /** 当前选中模型名（null = 用后端默认） */
  value: string | null
  onChange: (name: string) => void
  disabled?: boolean
}

const providerStyles: Record<string, string> = {
  openai: 'bg-emerald-500/10 text-emerald-600',
  deepseek: 'bg-blue-500/10 text-blue-600',
}

export function ModelSelector({ value, onChange, disabled }: ModelSelectorProps) {
  const { data: models, isLoading, isError, refetch } = useModels()
  const [open, setOpen] = useState(false)

  const current = models?.find((m) => m.name === value)

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        className="h-8 gap-1 text-xs font-normal text-muted-foreground"
        onClick={() => setOpen(true)}
        disabled={disabled}
      >
        <ChevronsUpDown className="size-3.5" />
        <span className="max-w-28 truncate">{current ? current.name : '默认模型'}</span>
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>选择模型</DialogTitle>
            <DialogDescription>
              选择运行本轮对话使用的模型；未指定的模型使用后端默认配置。
            </DialogDescription>
          </DialogHeader>

          <div className="max-h-72 overflow-y-auto rounded-md border p-1">
            {isLoading ? (
              <div className="flex flex-col gap-1 p-1">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            ) : isError ? (
              <div className="flex flex-col items-center gap-2 py-6 text-center text-sm text-muted-foreground">
                <span>模型列表加载失败</span>
                <Button variant="outline" size="sm" onClick={() => refetch()}>
                  <RefreshCw />
                  重试
                </Button>
              </div>
            ) : !models || models.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">暂无可用模型</p>
            ) : (
              <div className="flex flex-col gap-0.5">
                {models.map((m) => {
                  const selected = m.name === value
                  return (
                    <button
                      key={m.name}
                      type="button"
                      onClick={() => {
                        onChange(m.name)
                        setOpen(false)
                      }}
                      className={cn(
                        'flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors',
                        selected ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60',
                      )}
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{m.name}</span>
                        <span className="block truncate text-xs text-muted-foreground">{m.model}</span>
                      </span>
                      <Badge
                        variant="outline"
                        className={cn('shrink-0 text-xs', providerStyles[m.provider])}
                      >
                        {m.provider}
                      </Badge>
                      {m.supports_thinking && (
                        <Badge variant="secondary" className="shrink-0 text-xs">
                          思考
                        </Badge>
                      )}
                      {selected && <Check className="size-4 shrink-0" />}
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}