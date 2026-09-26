import { Loader2 } from '@/components/icons'

import { type TodoItem } from '@/core/runs/useRunStream'
import { cn } from '@/lib/utils'

type TodoStatus = 'pending' | 'in_progress' | 'completed' | 'cancelled' | 'unknown'
function normalizeStatus(raw: string | undefined): TodoStatus {
  const s = (raw ?? '').toLowerCase().trim()
  if (s === 'completed' || s === 'done' || s === 'success' || s === 'finished' || s === '✓')
    return 'completed'
  if (s === 'in_progress' || s === 'running' || s === 'working' || s === 'processing')
    return 'in_progress'
  // cancelled：没做完就结束（后端收尾把残留 in_progress 归位到这个值）——
  // 与 pending 区分开，用户能看出「做了一半被中断」和「还没轮到」
  if (s === 'cancelled' || s === 'canceled' || s === 'skipped' || s === 'abandoned')
    return 'cancelled'
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
    cancelled: todos.filter((t) => normalizeStatus(t.status) === 'cancelled').length,
  }

  const summary =
    counts.completed > 0 || counts.cancelled > 0
      ? `${counts.completed}/${todos.length} 完成${counts.cancelled > 0 && ` · ${counts.cancelled} 取消`}`
      : `${todos.length} 项`

  return (
    // 软表面面板：米色调半透明 + 细描边，贴进画布背景（不用白卡+投影的浮起形态）；
    // 圆角/描边与对话框、思考链面板同族（rounded-[14px]）。对齐由 ChatPage 外层负责。
    <section className="mb-1.5 overflow-hidden rounded-[14px] border border-border/60 bg-muted/40">
      <div className="flex items-center gap-2 px-3.5 pt-2.5 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground">
        <span>任务清单</span>
        <span className="text-muted-foreground/60">
          {summary}
          {counts.inProgress > 0 && ` · ${counts.inProgress} 进行中`}
        </span>
      </div>

      <div className="flex max-h-36 flex-col gap-0.5 overflow-y-auto px-2 pb-2">
        {todos.map((item, i) => {
          const st = normalizeStatus(item.status)
          return (
            <div
              key={item.id || i}
              className={cn(
                'flex items-center gap-2 rounded-[9px] px-2 py-1 text-xs transition-colors hover:bg-accent/40',
                // 进行中的条目铺一层极淡的主题色底，视线锚点
                st === 'in_progress' && 'bg-primary/[0.06]',
              )}
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
                      : st === 'cancelled'
                        ? 'bg-amber-500/70'
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
                  st === 'cancelled' && 'text-muted-foreground/60 line-through decoration-amber-600/50',
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
    </section>
  )
}