import { type ThreadOut } from '@/core/api/types'

export interface ThreadListQuery {
}

export interface ThreadCreatePayload {
  title: string
}

export interface ThreadRenamePayload {
  title: string
}

export type { ThreadOut }