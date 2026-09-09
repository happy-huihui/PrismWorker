import { type RunCreateBody, type RunOut, type RunStatus } from '@/core/api/types'

export interface RunCreatePayload extends RunCreateBody {
  threadId: string
}

export type { RunOut, RunStatus, RunCreateBody }