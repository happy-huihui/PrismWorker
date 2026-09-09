import { useCallback, useEffect, useReducer, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { readSSEStream, parseSSEData, type SSEFrame } from '@/core/api/sse'
import { USER_ID } from '@/core/api/client'
import { type RunCreateBody, type RunOut } from '@/core/api/types'
import { messageListKey } from '@/core/messages'
import { threadListKey } from '@/core/threads'

import { cancelRun, createRun, runStreamUrl } from './api'


export interface ToolCallItem {
  tool_call_id: string
  tool: string
  args_preview: string
  started_at: number
  finished_at: number | null
  duration_seconds: number | null
  status: 'running' | 'completed' | 'failed'
}

export interface TodoItem {
  id: string
  label?: string
  title?: string
  content?: string
  status: string
  [k: string]: unknown
}

export type RunStreamStatus =
  | 'idle'
  | 'connecting'
  | 'running'
  | 'finished'
  | 'cancelled'
  | 'error'

export interface RunStreamState {
  status: RunStreamStatus
  runId: string | null
  modelName: string | null
  thinkingEnabled: boolean | null
  error: string | null
  prints: string[]
  todos: TodoItem[]
  artifacts: string[]
  aiText: string
  /** 模型思考叙述累积（thinking_chunk 事件追加，进思考链展示） */
  thinkingText: string
  toolCalls: ToolCallItem[]
  messageCount: number | null
  startedAt: number | null
  finishedAt: number | null
  /** 终态瞬态标记（供结束动画/一次性提示） */
  justFinished: boolean
}

export const initialRunStreamState: RunStreamState = {
  status: 'idle',
  runId: null,
  modelName: null,
  thinkingEnabled: null,
  error: null,
  prints: [],
  todos: [],
  artifacts: [],
  aiText: '',
  thinkingText: '',
  toolCalls: [],
  messageCount: null,
  startedAt: null,
  finishedAt: null,
  justFinished: false,
}


type StreamAction =
  | { type: 'connect'; runId: string }
  | { type: 'started' }
  | { type: 'meta'; modelName: string | null; thinkingEnabled: boolean | null }
  | { type: 'tool_start'; item: ToolCallItem }
  | { type: 'tool_end'; toolCallId: string; durationSeconds: number | null }
  | { type: 'prints'; lines: string[] }
  | { type: 'todos'; todos: TodoItem[] }
  | { type: 'artifacts'; paths: string[] }
  | { type: 'chunk'; text: string }
  | { type: 'thinking'; text: string }
  | { type: 'finished'; messageCount: number | null; finishedAt: number | null }
  | { type: 'fail'; error: string }
  | { type: 'cancelled' }

function streamReducer(state: RunStreamState, action: StreamAction): RunStreamState {
  switch (action.type) {
    case 'connect':
      return {
        ...initialRunStreamState,
        status: 'connecting',
        runId: action.runId,
        startedAt: Date.now() / 1000,
      }
    case 'started':
      return { ...state, status: 'running' }
    case 'meta':
      return { ...state, modelName: action.modelName, thinkingEnabled: action.thinkingEnabled }
    case 'tool_start':
      return {
        ...state,
        status: 'running',
        toolCalls: [...state.toolCalls, action.item],
      }
    case 'tool_end':
      return {
        ...state,
        toolCalls: state.toolCalls.map((t) =>
          t.tool_call_id === action.toolCallId
            ? { ...t, status: 'completed', duration_seconds: action.durationSeconds, finished_at: Date.now() / 1000 }
            : t,
        ),
      }
    case 'prints':
      return {
        ...state,
        status: state.status === 'connecting' ? 'running' : state.status,
        prints: [...state.prints, ...action.lines],
      }
    case 'todos':
      return {
        ...state,
        status: state.status === 'connecting' ? 'running' : state.status,
        todos: action.todos,
      }
    case 'artifacts':
      return {
        ...state,
        status: state.status === 'connecting' ? 'running' : state.status,
        artifacts: [...state.artifacts, ...action.paths],
      }
    case 'chunk':
      return {
        ...state,
        status: state.status === 'connecting' ? 'running' : state.status,
        aiText: state.aiText + action.text,
      }
    case 'thinking':
      return {
        ...state,
        status: state.status === 'connecting' ? 'running' : state.status,
        thinkingText: state.thinkingText + action.text,
      }
    case 'finished':
      return {
        ...state,
        status: 'finished',
        messageCount: action.messageCount,
        finishedAt: action.finishedAt,
        justFinished: true,
      }
    case 'fail':
      return { ...state, status: 'error', error: action.error, justFinished: true }
    case 'cancelled':
      return { ...state, status: 'cancelled', justFinished: true }
    default:
      return state
  }
}


interface UseRunStreamOptions {
  /** 刷新恢复：挂载时若该 run 仍在运行（running/pending）自动重连流 */
  resumeRun?: RunOut | null
}

export function useRunStream(threadId: string, options?: UseRunStreamOptions) {
  const { resumeRun } = options ?? {}
  const qc = useQueryClient()
  const [state, dispatch] = useReducer(streamReducer, initialRunStreamState)

  const stateRef = useRef(state)
  stateRef.current = state
  const threadIdRef = useRef(threadId)
  threadIdRef.current = threadId
  const abortRef = useRef<AbortController | null>(null)
  const runningRef = useRef(false)

  const handleFrame = useCallback((frame: SSEFrame) => {
    const data = parseSSEData<Record<string, unknown>>(frame)
    if (data == null) return
    switch (frame.event) {
      case 'run_started':
        dispatch({ type: 'started' })
        break
      case 'run_meta':
        dispatch({
          type: 'meta',
          modelName: typeof data.model_name === 'string' ? data.model_name : null,
          thinkingEnabled: typeof data.thinking_enabled === 'boolean' ? data.thinking_enabled : null,
        })
        break
      case 'tool_start':
        dispatch({
          type: 'tool_start',
          item: {
            tool_call_id: String(data.tool_call_id ?? crypto.randomUUID()),
            tool: String(data.tool ?? 'tool'),
            args_preview: String(data.args_preview ?? ''),
            started_at: typeof data.ts === 'number' ? data.ts : Date.now() / 1000,
            finished_at: null,
            duration_seconds: null,
            status: 'running',
          },
        })
        break
      case 'tool_end':
        dispatch({
          type: 'tool_end',
          toolCallId: String(data.tool_call_id ?? ''),
          durationSeconds: typeof data.duration_seconds === 'number' ? data.duration_seconds : null,
        })
        break
      case 'prints':
        dispatch({
          type: 'prints',
          lines: Array.isArray(data.prints) ? data.prints.map(String) : [],
        })
        break
      case 'todos':
        dispatch({
          type: 'todos',
          todos: Array.isArray(data.todos) ? (data.todos as TodoItem[]) : [],
        })
        break
      case 'artifacts':
        dispatch({
          type: 'artifacts',
          paths: Array.isArray(data.artifacts) ? data.artifacts.map(String) : [],
        })
        break
      case 'message_chunk':
        dispatch({ type: 'chunk', text: typeof data.text === 'string' ? data.text : '' })
        break
      case 'thinking_chunk':
        dispatch({ type: 'thinking', text: typeof data.text === 'string' ? data.text : '' })
        break
      case 'run_finished':
        dispatch({
          type: 'finished',
          messageCount: typeof data.message_count === 'number' ? data.message_count : null,
          finishedAt: typeof data.finished_at === 'number' ? data.finished_at : null,
        })
        break
      case 'run_error':
        dispatch({
          type: 'fail',
          error: typeof data.error === 'string' && data.error ? data.error : '运行出错',
        })
        break
      default:
        break
    }
  }, [])

  const connect = useCallback(
    async (runId: string) => {
      if (runningRef.current) return
      runningRef.current = true
      abortRef.current = new AbortController()
      const signal = abortRef.current.signal
      dispatch({ type: 'connect', runId })

      try {
        const resp = await fetch(runStreamUrl(runId), {
          headers: { 'X-User-Id': USER_ID },
          signal,
        })
        if (!resp.ok) {
          const body = await resp.json().catch(() => null)
          throw new Error(
            (body as { detail?: string } | null)?.detail ?? `SSE 连接失败（HTTP ${resp.status}）`,
          )
        }
        await readSSEStream(resp, handleFrame, signal)
      } catch (err) {
        if (signal.aborted) {
          dispatch({ type: 'cancelled' })
        } else {
          const cur = stateRef.current.status
          if (cur !== 'finished' && cur !== 'error' && cur !== 'cancelled') {
            dispatch({ type: 'fail', error: err instanceof Error ? err.message : 'SSE 连接中断' })
          }
        }
      } finally {
        runningRef.current = false
        abortRef.current = null
        qc.invalidateQueries({ queryKey: messageListKey(threadIdRef.current) })
        qc.invalidateQueries({ queryKey: threadListKey })
      }
    },
    [handleFrame, qc],
  )

  const submit = useCallback(
    async (text: string, opts?: { model_name?: string | null; thinking_enabled?: boolean }) => {
      const trimmed = text.trim()
      if (runningRef.current || !trimmed) return
      try {
        const body: RunCreateBody = {
          messages: [{ type: 'human', content: trimmed }],
          model_name: opts?.model_name ?? null,
          thinking_enabled: opts?.thinking_enabled ?? false,
        }
        const run = await createRun(threadIdRef.current, body)
        void connect(run.run_id)
        return true
      } catch (err) {
        runningRef.current = false
        dispatch({ type: 'fail', error: err instanceof Error ? err.message : '启动运行失败' })
        return false
      }
    },
    [connect],
  )

  const stop = useCallback(async () => {
    const runId = stateRef.current.runId
    if (!runId || !runningRef.current) return
    abortRef.current?.abort()
    try {
      await cancelRun(runId)
    } catch {
    }
    dispatch({ type: 'cancelled' })
  }, [])

  useEffect(() => {
    if (!resumeRun) return
    const st = resumeRun.status
    if (st === 'running' || st === 'pending') {
      void connect(resumeRun.run_id)
    }
  }, [resumeRun, connect])

  useEffect(() => {
    return () => abortRef.current?.abort()
  }, [])

  return { state, submit, stop, connect }
}