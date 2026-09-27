import { useNavigate } from 'react-router-dom'

import { useAuth } from '@/app/providers/AuthProvider'
import { Plus } from '@/components/icons'
import { cn } from '@/lib/utils'

/**
 * 创建 Chat 按钮（NewChatButton）
 *
 * 职责：跳转到主页（Qoder 式「Quest on」首问页）——会话延迟创建：在主页发出
 *      首条消息时才真正建会话并自动发送，之后转底栏输入。因此连点/重复创建
 *      问题在结构上不存在；侧栏列表里也只会有真正发过消息的会话。
 * 快捷键：Ctrl/⌘ + N（由 ThreadSidebar 统一注册）；kbd 提示按平台自适应（Mac ⌘N，其余 Ctrl N）。
 */

// 平台判定一次即可（Mac 系显示 ⌘N，Windows/Linux 显示 Ctrl N）
const IS_MAC =
  typeof navigator !== 'undefined' &&
  /mac|iphone|ipad|ipod/i.test(navigator.platform || navigator.userAgent)
const NEW_CHAT_KBD = IS_MAC ? '⌘N' : 'Ctrl N'

export function NewChatButton({ collapsed }: { collapsed: boolean }) {
  const navigate = useNavigate()
  const { userId, openLogin } = useAuth()

  const handleNewChat = () => {
    // 未登录：弹登录框
    if (!userId) {
      openLogin()
      return
    }
    // 直接回主页：首问在那里发出时才建会话
    navigate('/')
  }

  return (
    <button
      type="button"
      onClick={handleNewChat}
      title="创建 Chat"
      className={cn(
        // 模板 .newchat：panel 底 + 细边 + 阴影，主行加号 + 右侧 kbd 快捷键
        'flex w-full items-center gap-2 rounded-xl border border-input bg-card px-3 py-2.5 text-sm font-medium text-foreground shadow-sm transition-colors hover:bg-accent',
        collapsed && 'justify-center px-0',
      )}
    >
      <Plus className="size-4 shrink-0" />
      {!collapsed && (
        <>
          <span className="flex-1 text-left">创建 Chat</span>
          <kbd className="shrink-0 font-mono text-[11px] text-muted-foreground">{NEW_CHAT_KBD}</kbd>
        </>
      )}
    </button>
  )
}
