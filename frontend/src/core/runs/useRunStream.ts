import { useCallback, useEffect, useReducer, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { readSSEStream, parseSSEData, type SSEFrame } from '@/core/api/sse'
import { authHeaders } from '@/core/auth/token'
import { type ChainEvent, type RunCreateBody, type RunOut } from '@/core/api/types'
import { messageListKey } from '@/core/messages'
import { threadListKey } from '@/core/threads'

import { cancelRun, createRun, runStreamUrl } from './api'


export interface ToolCallItem {
  tool_call_id: string
  tool: string
  /** 整包 JSON 预览（兼容旧数据；新数据优先用 args / description） */
  args_preview: string
  /** 模型自填的一行动作标题（DeerFlow 同款：卡片标题首选，缺失回退内置中文映射） */
  description?: string
  /** 结构化工具参数（按 path / command / query 等语义键渲染「目标」chip） */
  args?: Record<string, unknown>
  started_at: number
  finished_at: number | null
  duration_seconds: number | null
  status: 'running' | 'completed' | 'failed'
}

/**
 * 思考链一步（后端事件按到达顺序落成的时间线节点）
 *
 * - reasoning：模型真实思考（reasoning_content），同一条消息内原地增长
 * - narration：本轮叙述文本（先当答复流式，后因出现工具调用被降级过来）
 * - tool：一次工具调用（item 随 tool_end 更新耗时/成败）
 *
 * id 稳定（按 messageId / tool_call_id 派生），React 复用与回放顺序都靠它。
 */
export type ChainStep =
  | { kind: 'reasoning'; id: string; messageId: string; text: string }
  | { kind: 'narration'; id: string; messageId: string; text: string }
  | { kind: 'tool'; id: string; item: ToolCallItem }

export interface TodoItem {
  id: string
  label?: string
  title?: string
  content?: string
  status: string
  [k: string]: unknown
}

/** 各家模型表达「执行中」的同义词（与 TodosPanel.normalizeStatus 的口径保持一致）。 */
const TODO_IN_PROGRESS_STATUSES = new Set(['in_progress', 'running', 'working', 'processing'])

/**
 * run 到终态时归位任务清单：把还在「进行中」的条目降级为 pending。
 *
 * 为什么要做（2026-09-25 实测缺陷）：todos 是线程级持久的（thread_state 里
 * merge_todos 保留最近一次非空更新），上一轮被取消/中断的 run 会留下若干
 * in_progress 条目；而 finished/cancelled/fail 三个终态原先都不处理 todos，
 * 结果 run 都结束了、面板上还有两个转圈在谎报「执行中」，且会一直转到天荒地老
 * （模型不会再输出下一次更新了）。
 *
 * 为什么降级为 pending 而不是 completed：模型没说做完就是没做完，
 * 如实展示「未完成」比伪造一个绿色对勾诚实。
 */
function settleTodos(todos: TodoItem[]): TodoItem[] {
  return todos.map((t) => {
    const s = (t.status ?? '').toLowerCase().trim()
    return TODO_IN_PROGRESS_STATUSES.has(s) ? { ...t, status: 'pending' } : t
  })
}

export type RunStreamStatus =
  | 'idle'
  | 'connecting'
  | 'running'
  | 'finished'
  | 'cancelled'
  | 'error'

/**
 * 模型路由决策（后端 run_meta.routing 下发）
 *
 * 前端不再让用户选模型，改为展示「这轮用了哪个模型、为什么」。
 * source 决定展示文案的措辞（见 ThinkingChain 的路由提示）。
 */
export interface RoutingInfo {
  /** 最终使用的模型名（配置里的 name） */
  modelName: string
  /** 决策来源：explicit / vision / keyword / default / fallback */
  source: string
  /** 人类可读的中文原因，可直接展示 */
  reason: string
  /** source=keyword 时命中的关键词 */
  matchedKeyword: string | null
  /** 是否发生了升档（keyword / vision） */
  escalated: boolean
}

export interface RunStreamState {
  status: RunStreamStatus
  runId: string | null
  modelName: string | null
  thinkingEnabled: boolean | null
  /** 请求了思考但模型不支持，已被降级忽略（思考链里要说明，而不是报错） */
  thinkingDegraded: boolean
  /** 本轮的模型路由决策（无则未收到 run_meta） */
  routing: RoutingInfo | null
  error: string | null
  prints: string[]
  todos: TodoItem[]
  artifacts: string[]
  /** 最终答复文本 = 未被降级的模型消息文本按到达顺序拼接 */
  aiText: string
  /**
   * 按轮次键归并的答复文本（降级时要整段搬走）。
   * 轮次键 = `${messageId}#${round}`：DeepSeek 整个 run 复用同一个 message_id，
   * 只按 message_id 归并会让首轮的工具调用把后续所有正文一并吞掉。
   */
  answerByMessage: Record<string, string>
  /** 已被定性为思考叙述的轮次键（同上，含轮次） */
  retracted: Record<string, boolean>
  /** 每个 message_id 当前所处的轮次序号（工具结束即开新一轮） */
  roundByMessage: Record<string, number>
  /** 思考链时间线（reasoning / narration / tool 三类步骤，按到达顺序） */
  steps: ChainStep[]
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
  thinkingDegraded: false,
  routing: null,
  error: null,
  prints: [],
  todos: [],
  artifacts: [],
  aiText: '',
  answerByMessage: {},
  retracted: {},
  roundByMessage: {},
  steps: [],
  toolCalls: [],
  messageCount: null,
  startedAt: null,
  finishedAt: null,
  justFinished: false,
}


/**
 * 轮次键：同一 message_id 的不同轮次必须区分，否则首轮降级会吞掉后续全部正文。
 * （历史注释：旧版「先当答复、事后降级」模型曾用它拼接答复；现模型下它只做
 *   叙述步的分轮键与终态毕业的定位键。）
 */
const ROUND_SEP = '#'

function roundKey(messageId: string, round: number): string {
  return `${messageId}${ROUND_SEP}${round}`
}

/** 从轮次键还原 message_id（message_id 本身不含 `#`，取最后一个分隔符前的内容）。 */
function parseRoundKey(key: string): { messageId: string; round: number } {
  const at = key.lastIndexOf(ROUND_SEP)
  if (at < 0) return { messageId: key, round: 0 }
  const round = Number(key.slice(at + 1))
  return {
    messageId: key.slice(0, at),
    round: Number.isFinite(round) ? round : 0,
  }
}

/**
 * 工具结束 → 进入新一轮：把「本轮出现过的 message_id」轮次号 +1。
 *
 * 注意两点：
 *  1. 入参必须是**原始 message_id**，不能直接传 answerByMessage 的键
 *     （那些是 `mid#round` 形式的轮次键，传进来会写出一堆无用键、
 *     真正的轮次号永不推进，表现为最终答复仍被首轮降级吞掉）；
 *  2. 入参先**去重**——同一个 message_id 在 answerByMessage 里有多个轮次键，
 *     不去重会被 +1 多次导致轮次号跳号（`0 → 1 → 3 → 6`）。
 *     跳号本身不影响正确性（键只需逐轮唯一），但会让日志难读。
 */
function bumpRounds(
  roundByMessage: Record<string, number>,
  messageIds: Iterable<string>,
): Record<string, number> {
  const next = { ...roundByMessage }
  for (const id of new Set(messageIds)) {
    next[id] = (next[id] ?? 0) + 1
  }
  return next
}

/** 把一段思考/叙述文本落到时间线：同一条消息的后续增量原地追加，否则新开一步。 */
function appendTextStep(
  steps: ChainStep[],
  kind: 'reasoning' | 'narration',
  messageId: string,
  text: string,
): ChainStep[] {
  const id = `${kind}:${messageId}`
  const hit = steps.find((s) => s.id === id)
  if (hit) {
    return steps.map((s) =>
      s.id === id && (s.kind === 'reasoning' || s.kind === 'narration')
        ? { ...s, text: s.text + text }
        : s,
    )
  }
  // 思考先于该消息的叙述文本：降级过来的叙述排在同消息的思考步之后更贴近阅读顺序
  return [...steps, { kind, id, messageId, text } as ChainStep]
}

/**
 * 叙述步落时间线（轮次感知版）：DeepSeek 整个 run 复用同一 message_id，
 * 不同「轮次」的叙述必须各自成步（否则第二轮叙述会被追加进第一步的位置，
 * 时间线顺序错乱），所以 id 用 `narration:${messageId}#${round}`。
 */
function appendNarrationStep(
  steps: ChainStep[],
  key: string,
  messageId: string,
  text: string,
): ChainStep[] {
  const id = `narration:${key}`
  const hit = steps.find((s) => s.id === id)
  if (hit) {
    return steps.map((s) =>
      s.id === id && s.kind === 'narration' ? { ...s, text: s.text + text } : s,
    )
  }
  return [...steps, { kind: 'narration', id, messageId, text } as ChainStep]
}

/**
 * 终态「毕业」（对齐 DeerFlow getMessageGroups 的 becomesAssistantBubble / #4304）：
 * 最后一个**未被工具调用定性**的文本轮 = 本轮最终答复——把它从思考链里移出、
 * 放进答复气泡；其余文本轮留在链里当叙述。
 *
 * 为什么是「最后一个未定性轮」：带工具调用的轮次是叙述（定性事件 retract 已标），
 * 只有内容型消息才可能是答复；且回合结束后它必然是最后一个文本轮。
 * 若最后一轮本身就带工具调用（run 以工具收尾），则本轮没有答复（aiText 为空）。
 */
function graduateAnswer(state: RunStreamState): Pick<RunStreamState, 'aiText' | 'steps'> {
  const keys = Object.keys(state.answerByMessage)
  const lastKey = keys[keys.length - 1]
  const isAnswer = lastKey != null && !state.retracted[lastKey]
  if (!isAnswer || lastKey == null) {
    return { aiText: '', steps: state.steps }
  }
  return {
    aiText: state.answerByMessage[lastKey] ?? '',
    steps: state.steps.filter((s) => s.id !== `narration:${lastKey}`),
  }
}


type StreamAction =
  | { type: 'connect'; runId: string }
  | { type: 'reset' }
  | { type: 'started' }
  | { type: 'meta'; modelName: string | null; thinkingEnabled: boolean | null; thinkingDegraded?: boolean; routing?: RoutingInfo | null }
  | { type: 'tool_start'; item: ToolCallItem }
  | { type: 'tool_end'; toolCallId: string; durationSeconds: number | null; ok: boolean }
  | { type: 'prints'; lines: string[] }
  | { type: 'todos'; todos: TodoItem[] }
  | { type: 'artifacts'; paths: string[] }
  | { type: 'chunk'; messageId: string; text: string }
  | { type: 'reasoning'; messageId: string; text: string }
  | { type: 'retract'; messageId: string }
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
    case 'reset':
      return { ...initialRunStreamState }
    case 'started':
      // 收到 run_started（含回放）即算跑起来了：不再依赖第一个文本事件才脱离 connecting
      return { ...state, status: 'running' }
    case 'meta':
      return {
        ...state,
        modelName: action.modelName,
        thinkingEnabled: action.thinkingEnabled,
        thinkingDegraded: action.thinkingDegraded ?? false,
        // routing 缺省时保留旧值：历史落库的 run_meta 可能没有这个字段
        routing: action.routing ?? state.routing,
      }
    case 'tool_start':
      return {
        ...state,
        status: state.status === 'connecting' ? 'running' : state.status,
        toolCalls: [...state.toolCalls, action.item],
        steps: [
          ...state.steps,
          { kind: 'tool', id: `tool:${action.item.tool_call_id}`, item: action.item },
        ],
      }
    case 'tool_end': {
      const toolCalls = state.toolCalls.map((t) =>
        t.tool_call_id === action.toolCallId
          ? {
              ...t,
              // 失败要如实标红，不能一律归位 completed（工具步骤是思考链的主角）
              status: action.ok ? ('completed' as const) : ('failed' as const),
              duration_seconds: action.durationSeconds,
              finished_at: Date.now() / 1000,
            }
          : t,
      )
      const ended = toolCalls.find((t) => t.tool_call_id === action.toolCallId)
      return {
        ...state,
        toolCalls,
        // 工具执行完 = 这一轮结束，下一段正文属于新一轮（轮次键要 +1）。
        // 必须先把轮次键还原成原始 message_id，否则轮次号永远推不动。
        roundByMessage: bumpRounds(
          state.roundByMessage,
          Object.keys(state.answerByMessage).map((k) => parseRoundKey(k).messageId),
        ),
        steps: state.steps.map((s) =>
          s.kind === 'tool' && s.item.tool_call_id === action.toolCallId && ended
            ? { ...s, item: ended }
            : s,
        ),
      }
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
    case 'chunk': {
      const { messageId, text } = action
      const round = state.roundByMessage[messageId] ?? 0
      const key = roundKey(messageId, round)
      const answerByMessage = {
        ...state.answerByMessage,
        [key]: (state.answerByMessage[key] ?? '') + text,
      }
      return {
        ...state,
        status: state.status === 'connecting' ? 'running' : state.status,
        answerByMessage,
        // DeerFlow 模型（#4304）：流式期间文本一律在思考链里实时增长（叙述步），
        // 答复气泡留空——「这一段是不是最终答复」只有回合结束才可知，
        // 先进气泡再撤回会造成「答案出现又缩回去」的中途跳动。
        aiText: '',
        steps: appendNarrationStep(state.steps, key, messageId, text),
      }
    }
    case 'reasoning':
      return {
        ...state,
        status: state.status === 'connecting' ? 'running' : state.status,
        steps: appendTextStep(state.steps, 'reasoning', action.messageId, action.text),
      }
    case 'retract': {
      // 本轮（同一 message_id + 同一轮次）出现了工具调用 → 该轮文本定性为叙述。
      // DeerFlow 模型下文本从第一个 chunk 起就已在链里（见 chunk 分支），
      // 这里只需记录定性结果，供终态「毕业」时把真正的答复排除在外。
      // 注意只在**当前轮次**上定性：DeepSeek 整个 run 复用同一个 message_id，
      // 若按 message_id 永久定性，首轮叙述会把后续每一轮的正文全部吞掉。
      const { messageId } = action
      const key = roundKey(messageId, state.roundByMessage[messageId] ?? 0)
      if (state.retracted[key]) return state
      return {
        ...state,
        retracted: { ...state.retracted, [key]: true },
        aiText: '',
      }
    }
    case 'thinking':
      // 历史落库数据里的「整轮叙述」（旧版事件）：当作一段叙述进步骤时间线
      return {
        ...state,
        status: state.status === 'connecting' ? 'running' : state.status,
        steps: appendTextStep(state.steps, 'narration', 'legacy', action.text),
      }
    case 'finished': {
      // 终态毕业：最后一个未定性文本轮进入答复气泡（DeerFlow 模型）
      const graduated = graduateAnswer(state)
      return {
        ...state,
        status: 'finished',
        messageCount: action.messageCount,
        finishedAt: action.finishedAt,
        justFinished: true,
        // run 结束后不再有「执行中」：还在转圈的条目如实降级为未完成
        todos: settleTodos(state.todos),
        ...graduated,
      }
    }
    case 'fail':
      return {
        ...state,
        status: 'error',
        error: action.error,
        justFinished: true,
        // 中断/失败的 run 没有答复：文本留在链里如实展示
        aiText: '',
        todos: settleTodos(state.todos),
      }
    case 'cancelled':
      return {
        ...state,
        status: 'cancelled',
        justFinished: true,
        aiText: '',
        todos: settleTodos(state.todos),
      }
    default:
      return state
  }
}


/**
 * 解析 run_meta.routing 载荷（后端 RoutingDecision.to_meta() 的产物）。
 *
 * 后端老版本 / 其他来源的历史事件可能没有这个字段，解析不出来就返回 null，
 * 由展示层决定「没有路由信息时就不显示提示」。
 */
function parseRouting(raw: unknown): RoutingInfo | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const obj = raw as Record<string, unknown>
  const modelName = typeof obj.model_name === 'string' ? obj.model_name : ''
  const source = typeof obj.source === 'string' ? obj.source : ''
  if (!source) return null
  return {
    modelName,
    source,
    reason: typeof obj.reason === 'string' ? obj.reason : '',
    matchedKeyword: typeof obj.matched_keyword === 'string' ? obj.matched_keyword : null,
    escalated: obj.escalated === true,
  }
}

/**
 * 事件 → reducer 动作（实时 SSE 与历史回放共用一份）。
 *
 * 为什么收成一份：两条路径以前各写一份 switch，加一种事件就会不一致，
 * 结果是“实时看到的思考链”与“重开会话回放的思考链”对不上。
 */
function frameToAction(
  event: string,
  data: Record<string, unknown>,
): StreamAction | null {
  switch (event) {
    case 'run_started':
      return { type: 'started' }
    case 'run_meta':
      return {
        type: 'meta',
        modelName: typeof data.model_name === 'string' ? data.model_name : null,
        thinkingEnabled: typeof data.thinking_enabled === 'boolean' ? data.thinking_enabled : null,
        thinkingDegraded: data.thinking_degraded === true,
        routing: parseRouting(data.routing),
      }
    case 'tool_start':
      return {
        type: 'tool_start',
        item: {
          tool_call_id: String(data.tool_call_id ?? crypto.randomUUID()),
          tool: String(data.tool ?? 'tool'),
          args_preview: String(data.args_preview ?? ''),
          description: typeof data.description === 'string' ? data.description : '',
          args:
            data.args && typeof data.args === 'object' && !Array.isArray(data.args)
              ? (data.args as Record<string, unknown>)
              : undefined,
          started_at: typeof data.ts === 'number' ? data.ts : Date.now() / 1000,
          finished_at: null,
          duration_seconds: null,
          status: 'running',
        },
      }
    case 'tool_end':
      return {
        type: 'tool_end',
        toolCallId: String(data.tool_call_id ?? ''),
        durationSeconds: typeof data.duration_seconds === 'number' ? data.duration_seconds : null,
        // 旧落库数据无 ok 字段 → 当作成功（不能把历史步骤一律标红）
        ok: data.ok !== false,
      }
    case 'prints':
      return { type: 'prints', lines: Array.isArray(data.prints) ? data.prints.map(String) : [] }
    case 'todos':
      return { type: 'todos', todos: Array.isArray(data.todos) ? (data.todos as TodoItem[]) : [] }
    case 'artifacts':
      return { type: 'artifacts', paths: Array.isArray(data.artifacts) ? data.artifacts.map(String) : [] }
    case 'reasoning_chunk':
      return {
        type: 'reasoning',
        messageId: String(data.message_id ?? '-'),
        text: typeof data.text === 'string' ? data.text : '',
      }
    case 'message_chunk':
      return {
        type: 'chunk',
        messageId: String(data.message_id ?? '-'),
        text: typeof data.text === 'string' ? data.text : '',
      }
    case 'message_retract':
      return { type: 'retract', messageId: String(data.message_id ?? '-') }
    case 'thinking_chunk':
      // 旧版整轮叙述（历史数据）：无 message_id，整段当叙述进时间线
      return { type: 'thinking', text: typeof data.text === 'string' ? data.text : '' }
    default:
      return null
  }
}

/**
 * 回放一个历史 run 的事件流，重建 RunStreamState（供历史思考链渲染）。
 * 计时元信息（startedAt/finishedAt）取自 run 记录本身（终端事件未落库）。
 */
export function replayChainEvents(
  events: ChainEvent[],
  meta?: { startedAt?: number | null; finishedAt?: number | null },
): RunStreamState {
  let state: RunStreamState = { ...initialRunStreamState }
  for (const evt of events) {
    const action = frameToAction(evt.event, evt.data ?? {})
    if (action) state = streamReducer(state, action)
  }
  // 历史回放恒为终态：未闭合的工具步归位为完成，清 justFinished
  const toolCalls = state.toolCalls.map((t) =>
    t.status === 'running' ? { ...t, status: 'completed' as const } : t,
  )
  // 终态毕业与实时流同一规则：最后一个未定性文本轮成为该 run 的答复
  const graduated = graduateAnswer(state)
  return {
    ...state,
    toolCalls,
    // 同理：历史 run 里「进行中」的任务也不可能再推进了，一并归位
    todos: settleTodos(state.todos),
    status: 'finished',
    justFinished: false,
    startedAt: meta?.startedAt ?? state.startedAt,
    finishedAt: meta?.finishedAt ?? state.finishedAt,
    ...graduated,
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
  const prevThreadIdRef = useRef(threadId)
  /** 当前 SSE 连接所属线程（收尾 invalidate 用，切换线程后不得串线程刷新） */
  const connThreadRef = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const runningRef = useRef(false)
  /**
   * 连接代际：每次 connect / 切换线程递增。旧连接的异步收尾（catch/finally）
   * 只在代际一致时才允许 dispatch / 重置 ref——否则 abort 后的 'cancelled'
   * 会晚于 switch 的 'reset' 落地，把新会话的流状态污染成 cancelled；
   * finally 里无条件的 runningRef/abortRef 重置也会误伤新连接。
   */
  const connSeqRef = useRef(0)

  const handleFrame = useCallback((frame: SSEFrame) => {
    const data = parseSSEData<Record<string, unknown>>(frame)
    if (data == null) return
    // 终端事件单独处理（带自有字段语义），其余走共用映射
    if (frame.event === 'run_finished') {
      dispatch({
        type: 'finished',
        messageCount: typeof data.message_count === 'number' ? data.message_count : null,
        finishedAt: typeof data.finished_at === 'number' ? data.finished_at : null,
      })
      return
    }
    if (frame.event === 'run_error') {
      dispatch({
        type: 'fail',
        error: typeof data.error === 'string' && data.error ? data.error : '运行出错',
      })
      return
    }
    const action = frameToAction(frame.event, data)
    if (action) dispatch(action)
  }, [])

  const connect = useCallback(
    async (runId: string) => {
      if (runningRef.current) return
      runningRef.current = true
      const seq = ++connSeqRef.current
      connThreadRef.current = threadIdRef.current
      abortRef.current = new AbortController()
      const signal = abortRef.current.signal
      dispatch({ type: 'connect', runId })

      try {
        const resp = await fetch(runStreamUrl(runId), {
          headers: { ...authHeaders() },
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
        // 代际不符 = 已切换线程/被新连接取代，旧连接的收尾一律丢弃
        if (connSeqRef.current !== seq) return
        if (signal.aborted) {
          dispatch({ type: 'cancelled' })
        } else {
          const cur = stateRef.current.status
          if (cur !== 'finished' && cur !== 'error' && cur !== 'cancelled') {
            dispatch({ type: 'fail', error: err instanceof Error ? err.message : 'SSE 连接中断' })
          }
        }
      } finally {
        const tid = connThreadRef.current
        if (connSeqRef.current === seq) {
          runningRef.current = false
          abortRef.current = null
          connThreadRef.current = null
        }
        if (tid) {
          qc.invalidateQueries({ queryKey: messageListKey(tid) })
          qc.invalidateQueries({ queryKey: threadListKey })
        }
      }
    },
    [handleFrame, qc],
  )

  const submit = useCallback(
    async (text: string, opts?: { model_name?: string | null; thinking_enabled?: boolean }) => {
      const trimmed = text.trim()
      if (runningRef.current || !trimmed) return
      // 回车即占位：先本地进 connecting（“正在处理…”），不等 POST、更不等后端首个事件。
      // 后端建 run 虽快，但装配/模型首字可能几秒，这段对等时间必须有反馈。
      dispatch({ type: 'connect', runId: '' })
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

  // 线程切换：中止旧流、清空本地流式状态，避免上一轮内容串到新线程。
  // 先推进连接代际再 abort：旧连接的异步收尾据此识别自己已作废，
  // 不会把 'cancelled' 盖到 reset 之后的新会话状态上。
  useEffect(() => {
    if (prevThreadIdRef.current === threadId) return
    prevThreadIdRef.current = threadId
    threadIdRef.current = threadId
    connSeqRef.current++
    abortRef.current?.abort()
    abortRef.current = null
    runningRef.current = false
    connThreadRef.current = null
    dispatch({ type: 'reset' })
  }, [threadId])

  useEffect(() => {
    return () => {
      connSeqRef.current++
      abortRef.current?.abort()
    }
  }, [])

  return { state, submit, stop, connect }
}