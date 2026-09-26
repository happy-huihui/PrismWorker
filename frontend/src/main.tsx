import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router-dom'

import './index.css'
import { AuthProvider } from './app/providers/AuthProvider'
import { BackgroundProvider } from './app/providers/BackgroundProvider'
import { router } from './app/router'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BackgroundProvider>
        <AuthProvider>
          <RouterProvider router={router} />
        </AuthProvider>
      </BackgroundProvider>
    </QueryClientProvider>
  </StrictMode>,
)