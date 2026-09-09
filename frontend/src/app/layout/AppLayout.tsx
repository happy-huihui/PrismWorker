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
        <Outlet />
      </main>
      <Toaster position="top-center" richColors />
    </div>
  )
}