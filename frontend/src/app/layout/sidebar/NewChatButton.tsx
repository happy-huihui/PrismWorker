import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { Loader2, Plus } from '@/components/icons'

import { useAuth } from '@/app/providers/AuthProvider'
import { useCreateThread } from '@/core/threads'
import { cn } from '@/lib/utils'

/**
 * 创建 Chat 按钮（NewChatButton）
 *
 * 职责：新建会话并跳转到该会话（对应 qoder 的"创建 Quest"，本项目语义是 Chat）。
 * 快捷键：Ctrl/⌘ + N（由 ThreadSidebar 统一注册）；kbd 提示按平台自适应（Mac ⌘N，其余 Ctrl N）。
 */

// 平台判定一次即可（Mac 系显示 ⌘N，Windows/Linux 显示 Ctrl N）
const IS_MAC =
  typeof navigator !== 'undefined' &&
  /mac|iphone|ipad|ipod/i.test(navigator.platform || navigator.userAgent)
const NEW_CHAT_KBD = IS_MAC ? '⌘N' : 'Ctrl N'

export function NewChatButton({ collapsed }: { collapsed: boolean }) {
  const navigate = useNavigate()
  const createThread = useCreateThread()
  const { userId, openLogin } = useAuth()

  const handleNewChat = () => {
    // 0.未登录：弹登录框，不建会话
    if (!userId) {
      openLogin()
      return
    }
    // 1.创建线程，成功后进入新会话
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
    <button
      type="button"
      onClick={handleNewChat}
      disabled={createThread.isPending}
      title="创建 Chat"
      className={cn(
        // 模板 .newchat：panel 底 + 细边 + 阴影，主行加号 + 右侧 kbd 快捷键
        'flex w-full items-center gap-2 rounded-xl border border-input bg-card px-3 py-2.5 text-sm font-medium text-foreground shadow-sm transition-colors hover:bg-accent disabled:opacity-60',
        collapsed && 'justify-center px-0',
      )}
    >
      {createThread.isPending ? (
        <Loader2 className="size-4 shrink-0 animate-spin" />
      ) : (
        <Plus className="size-4 shrink-0" />
      )}
      {!collapsed && (
        <>
          <span className="flex-1 text-left">创建 Chat</span>
          <kbd className="shrink-0 font-mono text-[11px] text-muted-foreground">{NEW_CHAT_KBD}</kbd>
        </>
      )}
    </button>
  )
}
