import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { MoreHorizontal, Pencil, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { type ThreadOut } from '@/core/api/types'
import { cn } from '@/lib/utils'

import { DeleteConfirmDialog } from './DeleteConfirmDialog'
import { RenameDialog } from './RenameDialog'

interface SidebarItemProps {
  thread: ThreadOut
  collapsed: boolean
}

export function SidebarItem({ thread, collapsed }: SidebarItemProps) {
  const [renameOpen, setRenameOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)

  if (collapsed) {
    return (
      <NavLink
        to={`/chats/${thread.thread_id}`}
        title={thread.title}
        className={({ isActive }) =>
          cn(
            'flex size-10 items-center justify-center rounded-md text-sm font-medium transition-colors',
            isActive ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
          )
        }
      >
        {thread.title.trim().charAt(0).toUpperCase() || '#'}
      </NavLink>
    )
  }

  return (
    <div className="group relative">
      <NavLink
        to={`/chats/${thread.thread_id}`}
        className={({ isActive }) =>
          cn(
            'flex w-full items-center rounded-md px-3 py-2 pr-8 text-sm transition-colors',
            isActive ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60',
          )
        }
      >
        <span className="truncate font-medium leading-tight">{thread.title}</span>
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