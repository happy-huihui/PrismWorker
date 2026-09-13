import { useState } from 'react'
import { Outlet } from 'react-router-dom'

import { Toaster } from '@/components/ui/sonner'

import { ThreadSidebar } from './ThreadSidebar'

export function AppLayout() {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem('prism-sidebar') === '1'
    } catch {
      return false
    }
  })

  const handleToggle = () => {
    setCollapsed((prev) => {
      const next = !prev
      try {
        localStorage.setItem('prism-sidebar', next ? '1' : '0')
      } catch {
      }
      return next
    })
  }

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background text-foreground">
      <ThreadSidebar collapsed={collapsed} onToggle={handleToggle} />
      <main className="relative flex min-w-0 flex-1 flex-col">
        {/* 顶部极淡的氛围光晕，与 body 背景呼应，避免页面生硬 */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-64 bg-gradient-to-b from-primary/[0.03] to-transparent"
        />
        <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
          <Outlet />
        </div>
      </main>
      <Toaster position="top-center" richColors />
    </div>
  )
}