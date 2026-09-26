import { useMemo, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { Lock, MoreHorizontal, Pencil, Search, Trash2 } from '@/components/icons'

import { useAuth } from '@/app/providers/AuthProvider'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Input } from '@/components/ui/input'
import { useThreads, type ThreadOut } from '@/core/threads'
import { formatThreadTime } from '@/lib/format'
import { cn } from '@/lib/utils'

import { DeleteConfirmDialog } from '../DeleteConfirmDialog'
import { RenameDialog } from '../RenameDialog'

/**
 * 会话列表（ThreadList）
 *
 * 职责：拉取线程列表、本地关键词过滤、渲染每一行（激活高亮 + 悬停出现重命名/删除菜单）。
 * 说明：后端无搜索接口，这里是纯前端按标题过滤已加载的会话（不新增后端能力）。
 */
export function ThreadList({ collapsed }: { collapsed: boolean }) {
  const { userId, openLogin } = useAuth()
  // 未登录：不发列表请求（后端也会 401，但前端先拦更干净）
  const { data: threads, isLoading, isError, refetch } = useThreads({ enabled: !!userId })
  const [query, setQuery] = useState('')

  // 1.按关键词（不区分大小写）过滤标题
  const filtered = useMemo(() => {
    const list = threads ?? []
    const q = query.trim().toLowerCase()
    if (!q) return list
    return list.filter((t) => t.title.toLowerCase().includes(q))
  }, [threads, query])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 2.搜索框（模板 .search：13px、细边、10px 圆角，折叠时隐藏） */}
      {!collapsed && (
        <div className="px-3.5 pb-2">
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索会话"
              className="h-[34px] rounded-[10px] border-border bg-transparent pl-8 text-[13px] shadow-none"
            />
          </div>
        </div>
      )}

      {/* 2.5分节标签「会话」（模板 .sec：12/18/4 内边距，字距 0.12em 大写） */}
      {!collapsed && (
        <div className="px-[18px] pt-3 pb-1 text-[11px] tracking-[0.12em] text-muted-foreground uppercase">
          会话
        </div>
      )}

      <div className="min-h-0 flex-1">
        {!userId ? (
          /* 0.未登录：登录引导（代替旧的「加载失败」，语义更准） */
          <button
            type="button"
            onClick={openLogin}
            className={cn(
              'mx-2 mt-2 flex items-center gap-2 rounded-[10px] border border-dashed px-2.5 py-2.5 text-left text-xs text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground',
              collapsed && 'justify-center px-0',
            )}
          >
            <Lock className="size-3.5 shrink-0" />
            {!collapsed && <span>登录后查看会话</span>}
          </button>
        ) : isLoading ? (
          <div className={cn('flex flex-col gap-1 px-2', collapsed && 'items-center px-0')}>
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className={cn('h-10 rounded-lg', collapsed ? 'size-10' : 'w-full')} />
            ))}
          </div>
        ) : isError ? (
          <div className="flex flex-col items-center gap-2 px-3 py-6 text-center text-xs text-muted-foreground">
            <span>会话列表加载失败</span>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              重试
            </Button>
          </div>
        ) : filtered.length === 0 ? (
          <p className={cn('px-3 py-6 text-center text-xs text-muted-foreground', collapsed && 'px-1')}>
            {collapsed ? '·' : query ? '没有匹配的会话' : '还没有会话'}
          </p>
        ) : (
          <ScrollArea className="h-full">
            {/* 模板 .list：左右 8px 内边距，行间距紧 */}
            <div className="flex flex-col gap-0.5 px-2 pb-2">
              {filtered.map((t) => (
                <ThreadRow key={t.thread_id} thread={t} collapsed={collapsed} />
              ))}
            </div>
          </ScrollArea>
        )}
      </div>
    </div>
  )
}

/** 单行会话：折叠态显示首字母方块，展开态显示标题 + 悬停操作菜单。 */
function ThreadRow({ thread, collapsed }: { thread: ThreadOut; collapsed: boolean }) {
  const [renameOpen, setRenameOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)

  // 1.折叠态：仅首字母头像，链接到会话
  if (collapsed) {
    return (
      <NavLink
        to={`/chats/${thread.thread_id}`}
        title={thread.title}
        className={({ isActive }) =>
          cn(
            'flex size-10 items-center justify-center rounded-lg text-sm font-medium transition-colors',
            isActive
              ? 'bg-accent text-accent-foreground'
              : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
          )
        }
      >
        {thread.title.trim().charAt(0).toUpperCase() || '#'}
      </NavLink>
    )
  }

  // 2.展开态：圆点 + 标题 + 紧凑时间（模板 .item：9/10 内边距、10px 圆角、14px 字，
  //   激活行 accent-soft 底 + 主色圆点；悬停时隐去时间、浮现操作菜单）
  return (
    <div className="group relative">
      <NavLink
        to={`/chats/${thread.thread_id}`}
        className={({ isActive }) =>
          cn(
            'flex w-full items-center gap-2.5 rounded-[10px] py-[9px] pr-3 pl-2.5 text-sm transition-colors',
            isActive
              ? 'bg-primary/10 font-medium text-foreground'
              : 'text-foreground/80 hover:bg-accent/50 hover:text-foreground',
          )
        }
      >
        {({ isActive }) => (
          <>
            {/* 圆点：活动态用主色，否则中性 */}
            <span
              className={cn(
                'size-1.5 shrink-0 rounded-full transition-colors',
                isActive ? 'bg-primary' : 'bg-muted-foreground/40',
              )}
            />
            <span className="min-w-0 flex-1 truncate leading-tight">{thread.title}</span>
            {/* 时间：悬停淡出，给右侧菜单让位 */}
            <span className="shrink-0 text-[11px] text-muted-foreground/70 tabular-nums transition-opacity group-hover:opacity-0">
              {formatThreadTime(thread.updated_at)}
            </span>
          </>
        )}
      </NavLink>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="absolute top-1/2 right-1 size-6 -translate-y-1/2 rounded-md text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100"
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
            }}
            onPointerDown={(e) => e.stopPropagation()}
          >
            <MoreHorizontal className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-32">
          <DropdownMenuItem onClick={() => setRenameOpen(true)}>
            <Pencil />
            重命名
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onClick={() => setDeleteOpen(true)}>
            <Trash2 />
            删除
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <RenameDialog thread={thread} open={renameOpen} onOpenChange={setRenameOpen} />
      <DeleteConfirmDialog thread={thread} open={deleteOpen} onOpenChange={setDeleteOpen} />
    </div>
  )
}
