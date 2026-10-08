import { createBrowserRouter } from 'react-router-dom'

import { AppLayout } from './layout/AppLayout'
import { ObservabilityLayout } from './layout/ObservabilityLayout'
import { ChatPage } from './pages/ChatPage'
import { AnalyticsPage } from './pages/observability/AnalyticsPage'
import { EvalsPage } from './pages/observability/EvalsPage'
import { OverviewPage } from './pages/observability/OverviewPage'
import { RunDetailPage } from './pages/observability/RunDetailPage'
import { RunsPage } from './pages/observability/RunsPage'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <ChatPage /> },
      { path: 'chats/:threadId', element: <ChatPage /> },
    ],
  },
  // 观测台：独立整页布局（脱离聊天壳），仅管理员可见入口
  {
    path: '/observability',
    element: <ObservabilityLayout />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: 'runs', element: <RunsPage /> },
      { path: 'runs/:runId', element: <RunDetailPage /> },
      { path: 'analytics', element: <AnalyticsPage /> },
      { path: 'evals', element: <EvalsPage /> },
    ],
  },
])
