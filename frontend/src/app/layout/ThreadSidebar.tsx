import { useNavigate } from 'react-router-dom'
import { Loader2, MessageSquarePlus, PanelLeftClose, PanelLeftOpen, Sparkles } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { useCreateThread, useThreads } from '@/core/threads'
import { cn } from '@/lib/utils'

import { SidebarItem } from './SidebarItem'
import { ThemeToggle } from './ThemeToggle'

interface ThreadSidebarProps {
  /** 是否折叠成窄条（仅图标） */
  collapsed: boolean
  /** 切换折叠（由 AppLayout 持有状态） */
  onToggle: () => void
}

export function ThreadSidebar({ collapsed, onToggle }: ThreadSidebarProps) {
  const navigate = useNavigate()
  const { data: threads, isLoading, isError, refetch } = useThreads()
  const createThread = useCreateThread()

  const handleNewChat = () => {
    createThread.mutate(
      { title: '新对话' },
      {
        onSuccess: (thread) => navigate(`/chats/${thread.thread_id}`),
        onError: (err) =>
          toast.error('新建会话失败', {
            description: err instanceof Error ? err.message : '未知错误',
          }),
      },
    )
  }

  return (
    <aside
      data-collapsed={collapsed}
      className={cn(
        'flex h-full shrink-0 flex-col border-r bg-background transition-[width] duration-200',
        collapsed ? 'w-14' : 'w-64',
      )}
    >
      <div className={cn('flex items-center gap-2 border-b px-3 py-3', collapsed && 'flex-col gap-3 border-b px-2')}>
        <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <Sparkles className="size-4" />
        </div>
        {!collapsed && <span className="truncate text-sm font-semibold tracking-tight">PrismWorker</span>}
        <Button
          variant="ghost"
          size={collapsed ? 'icon' : 'sm'}
          className={cn('ml-auto h-8 shrink-0', collapsed && 'ml-0')}
          onClick={handleNewChat}
          disabled={createThread.isPending}
        >
          {createThread.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <MessageSquarePlus className="size-4" />
          )}
          {!collapsed && <span>新建会话</span>}
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden py-2">
        {isLoading ? (
          <div className={cn('flex flex-col gap-1 px-2', collapsed && 'items-center px-0')}>
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className={cn('h-10 rounded-md', collapsed ? 'size-10' : 'w-full')} />
            ))}
          </div>
        ) : isError ? (
          <div className="flex flex-col items-center gap-2 px-3 py-6 text-center text-xs text-muted-foreground">
            <span>线程列表加载失败</span>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              重试
            </Button>
          </div>
        ) : threads == null || threads.length === 0 ? (
          <p className={cn('px-3 py-6 text-center text-xs text-muted-foreground', collapsed && 'px-1')}>
            {collapsed ? '·' : '还没有会话\n点击上方「新建会话」开始'}
          </p>
        ) : collapsed ? (
          <div className="flex flex-col items-center gap-1 px-1">
            {threads.map((t) => (
              <SidebarItem key={t.thread_id} thread={t} collapsed />
            ))}
          </div>
        ) : (
          <ScrollArea className="h-full">
            <div className="flex flex-col gap-0.5 px-2">
              {threads.map((t) => (
                <SidebarItem key={t.thread_id} thread={t} collapsed={false} />
              ))}
            </div>
          </ScrollArea>
        )}
      </div>

      <div className={cn('flex items-center gap-1 border-t p-2', collapsed && 'flex-col gap-2')}>
        <ThemeToggle />
        <Button
          variant="ghost"
          size="icon"
          className={cn('size-8', collapsed && '')}
          onClick={onToggle}
          title={collapsed ? '展开侧边栏' : '折叠侧边栏'}
        >
          {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
        </Button>
      </div>
    </aside>
  )
}