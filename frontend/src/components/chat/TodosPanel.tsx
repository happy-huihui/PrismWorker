import { Loader2 } from 'lucide-react'

import { type TodoItem } from '@/core/runs/useRunStream'
import { cn } from '@/lib/utils'

type TodoStatus = 'pending' | 'in_progress' | 'completed' | 'unknown'
function normalizeStatus(raw: string | undefined): TodoStatus {
  const s = (raw ?? '').toLowerCase().trim()
  if (s === 'completed' || s === 'done' || s === 'success' || s === 'finished' || s === '✓')
    return 'completed'
  if (s === 'in_progress' || s === 'running' || s === 'working' || s === 'processing')
    return 'in_progress'
  if (s === 'pending' || s === 'todo' || s === 'queued' || s === 'not_started' || s === '')
    return 'pending'
  return 'unknown'
}

function todoText(item: TodoItem): string {
  return item.label ?? item.title ?? item.content ?? item.id ?? ''
}

interface TodosPanelProps {
  todos: TodoItem[]
}

export function TodosPanel({ todos }: TodosPanelProps) {
  if (todos.length === 0) return null

  const counts = {
    pending: todos.filter((t) => normalizeStatus(t.status) === 'pending').length,
    inProgress: todos.filter((t) => normalizeStatus(t.status) === 'in_progress').length,
    completed: todos.filter((t) => normalizeStatus(t.status) === 'completed').length,
  }

  return (
    <div className="mb-1.5 flex flex-col gap-1">
      <div className="flex items-center gap-2 px-1 text-xs font-medium text-muted-foreground">
        <span>任务清单</span>
        <span className="text-muted-foreground/60">
          {counts.completed}/{todos.length} 完成
          {counts.inProgress > 0 && ` · ${counts.inProgress} 进行中`}
        </span>
      </div>

      <div className="flex max-h-32 flex-col gap-1 overflow-y-auto rounded-lg border bg-card/70 p-1.5">
        {todos.map((item, i) => {
          const st = normalizeStatus(item.status)
          return (
            <div
              key={item.id || i}
              className="flex items-center gap-2 rounded px-2 py-1 text-xs"
              title={item.status ? `状态：${item.status}` : undefined}
            >
              {st === 'in_progress' ? (
                <Loader2 className="size-3 shrink-0 animate-spin text-sky-500" />
              ) : (
                <span
                  className={cn(
                    'size-1.5 shrink-0 rounded-full',
                    st === 'completed'
                      ? 'bg-emerald-500'
                      : st === 'unknown'
                        ? 'bg-muted-foreground/40'
                        : 'bg-zinc-400 dark:bg-zinc-500',
                  )}
                />
              )}
              <span
                className={cn(
                  'min-w-0 flex-1 truncate',
                  st === 'completed' && 'text-muted-foreground/60 line-through',
                  st === 'in_progress' && 'text-foreground',
                  st === 'pending' && 'text-muted-foreground',
                )}
              >
                {todoText(item) || `任务 ${i + 1}`}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}