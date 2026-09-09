

export interface ThreadOut {
  thread_id: string
  user_id: string
  title: string
  /** 创建时间戳（秒） */
  created_at: number
  /** 最后更新时间戳（秒） */
  updated_at: number
  /** 累计消息数 */
  message_count: number
  /** 最后消息预览 */
  last_message_preview: string
  /** 线程状态：idle / running */
  status: 'idle' | 'running' | string
}


export type RunStatus = 'pending' | 'running' | 'finished' | 'cancelled' | 'error'

export interface RunOut {
  run_id: string
  thread_id: string
  user_id: string
  status: RunStatus
  model_name: string
  input_preview: string
  /** 错误摘要（error 状态） */
  error: string | null
  /** 交付物虚拟路径 */
  artifacts: string[]
  message_count: number
  created_at: number
  started_at: number | null
  finished_at: number | null
}

export interface RunCreateBody {
  messages: Array<{ type: string; content: string }>
  model_name?: string | null
  thinking_enabled?: boolean
}


export interface ModelOut {
  name: string
  provider: 'openai' | 'deepseek' | string
  model: string
  supports_vision: boolean
  supports_thinking: boolean
}


export interface MessageOut {
  /** role：user / assistant / tool / system / message */
  role: string
  content: string
}


export interface UploadOut {
  filename: string
  size: number
  /** 沙箱虚拟路径（/mnt/user-data/uploads/...） */
  virtual_path: string
}


export interface ApiErrorShape {
  detail?: string | Array<{ msg?: string; [k: string]: unknown }>
}

export interface HealthOut {
  status: string
}