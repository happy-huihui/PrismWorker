import { createBrowserRouter } from 'react-router-dom'

import { AppLayout } from './layout/AppLayout'
import { ChatPage } from './pages/ChatPage'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <ChatPage /> },
      { path: 'chats/:threadId', element: <ChatPage /> },
    ],
  },
])