import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

import { useCreateThread } from '@/core/threads'
import { useAuth } from '@/app/providers/AuthProvider'
import { cn } from '@/lib/utils'

import { NewChatButton } from './sidebar/NewChatButton'
import { SidebarFooter } from './sidebar/SidebarFooter'
import { SidebarHeader } from './sidebar/SidebarHeader'
import { ThreadList } from './sidebar/ThreadList'

/**
 * 会话侧栏（ThreadSidebar）
 *
 * 职责：组合侧栏各模块（顶部品牌/折叠、创建 Chat、会话列表、底部用户+背景+设置），
 *      并注册全局「⌘/Ctrl+N 新建会话」快捷键。折叠状态由 AppLayout 持有。
 */
interface ThreadSidebarProps {
  collapsed: boolean
  onToggle: () => void
}

export function ThreadSidebar({ collapsed, onToggle }: ThreadSidebarProps) {
  const navigate = useNavigate()
  const createThread = useCreateThread()
  const { userId, openLogin } = useAuth()

  // 1.全局快捷键：Ctrl/⌘ + N 新建会话（阻止浏览器默认新窗口）；未登录则弹登录框
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'n') {
        e.preventDefault()
        if (!userId) {
          openLogin()
          return
        }
        if (createThread.isPending) return
        createThread.mutate(
          { title: '新对话' },
          { onSuccess: (thread) => navigate(`/chats/${thread.thread_id}`) },
        )
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [createThread, navigate, userId, openLogin])

  return (
    <aside
      className={cn(
        // 模板 .sidebar：264px 固定宽、右侧分隔线、panel-2 底色 + 背景模糊
        'flex h-full shrink-0 flex-col border-r bg-sidebar backdrop-blur-sm transition-[width] duration-200',
        collapsed ? 'w-14' : 'w-[264px]',
      )}
    >
      <SidebarHeader collapsed={collapsed} onToggle={onToggle} />
      {/* 模板 .newchat：水平边距 14px、与搜索框间距 10px */}
      <div className={cn('px-3.5 pb-2.5', collapsed && 'px-2')}>
        <NewChatButton collapsed={collapsed} />
      </div>
      <ThreadList collapsed={collapsed} />
      <SidebarFooter collapsed={collapsed} />
    </aside>
  )
}
