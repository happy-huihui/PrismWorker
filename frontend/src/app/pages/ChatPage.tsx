import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Sparkles } from '@/components/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ChatInput } from '@/components/chat/ChatInput'
import { MessageList, type RunActivity, type RunStatusView } from '@/components/chat/MessageList'
import { TodosPanel } from '@/components/chat/TodosPanel'
import { ChatHeader } from '@/app/layout/chat/ChatHeader'
import { Composer } from '@/app/layout/chat/Composer'
import { TurnIndex } from '@/app/layout/chat/TurnIndex'
import { useAuth } from '@/app/providers/AuthProvider'
import { AttachmentList, type AttachmentItem } from '@/components/chat/AttachmentList'
import { ArtifactSidePanel } from '@/components/artifacts/ArtifactSidePanel'
import { ArtifactsProvider, useArtifacts } from '@/core/artifacts/context'
import { useMessages } from '@/core/messages'
import { threadListKey, useCreateThread, useThreads } from '@/core/threads'
import { useRunStream, useThreadChains, useThreadRuns, chainListKey, replayChainEvents } from '@/core/runs'
import { uploadFile } from '@/core/uploads'
import { type MessageOut, type ThreadOut } from '@/core/api/types'

export function ChatPage() {
  const { threadId } = useParams()
  // key=threadId 强制按会话重挂载：pendingUser / draft / attachments / 流状态
  // 都是会话级本地状态，不重置会从上一条会话泄漏到下一条（会话错乱来源之一）
  return threadId ? <ThreadView key={threadId} threadId={threadId} /> : <WelcomeView />
}

function loadLocal(key: string, fallback: string) {
  try {
    return localStorage.getItem(key) ?? fallback
  } catch {
    return fallback
  }
}
function saveLocal(key: string, v: string) {
  try {
    localStorage.setItem(key, v)
  } catch {
  }
}

const USER_INPUT_BEGIN = '--- BEGIN USER INPUT ---'
const USER_INPUT_END = '--- END USER INPUT ---'
const NEUTRALIZED_BOUNDARY_LINE_RE = /^\s*\[(?:BEGIN|END) USER INPUT\]\s*$/gm

/** 归一化 user 历史消息内容（剥离会话包裹标记），用于与待发文本比较 */
function normalizeUserContent(text: string): string {
  let cur = text
  for (let i = 0; i < 16; i++) {
    const b = cur.indexOf(USER_INPUT_BEGIN)
    const e = cur.lastIndexOf(USER_INPUT_END)
    if (b < 0 || e <= b) break
    cur = cur.slice(b + USER_INPUT_BEGIN.length, e)
  }
  return cur.replace(NEUTRALIZED_BOUNDARY_LINE_RE, '').replace(/\n{3,}/g, '\n\n').trim()
}

/**
 * 历史里那条 assistant 回复是否已经覆盖了本轮流式答复。
 *
 * 为什么不能直接 `historyText === aiText`：流式 aiText 是逐 token 拼出来的原始
 * 文本，而落库/重拉回来的历史会经过剥离防注入标记、摘除 `<finish/>`、空白归一
 * 等处理，二者常有细微差异（多一个换行、少一个尾标点）。严格相等一旦判否，
 * 就会把整段答复重复 push 一次，等 invalidate 重拉回来又消失 —— 视觉上就是
 * 答复「闪一下 / 尾段重复」。这里改判「历史尾部与答复尾部一致」即可。
 */
function historyCoversAnswer(historyText: string, aiText: string): boolean {
  const a = historyText.trim()
  const b = aiText.trim()
  if (!a) return false
  if (a === b) return true
  // 取较短的那个尾部片段做包含判断：历史已覆盖答复（历史通常更「干净」）
  const probe = b.slice(-Math.min(80, b.length))
  return probe.length > 0 && a.endsWith(probe)
}

/**
 * 欢迎页推荐话题池：按「种类」分组，每类内置几条。
 * 抽取规则见 pickSuggestions：每次进欢迎页随机挑 3 个种类、每类随机取 1 条，
 * 保证三条话题跨类不重复，刷新即换一批。
 */
const WELCOME_TOPIC_POOL: string[][] = [
  // 概念讲解类
  [
    '用通俗易懂的方式给我讲解堆排序',
    '如何理解 React 的 useMemo 与 useCallback？',
    '用比喻讲讲数据库索引为什么能加速查询',
    'TCP 三次握手和四次挥手区别是什么？',
  ],
  // 写作总结类
  [
    '帮我写一份项目周报的结构大纲',
    '给我一份技术博客的选题清单',
    '帮我把一段口语化文字改写成正式邮件',
    '总结一份 Git 常用命令速查表',
  ],
  // 代码实战类
  [
    '写一个校验手机号的正则表达式并解释',
    'Python 的 dataclass 和普通类有什么区别？',
    'SQL 查询慢通常从哪几个方面排查？',
    'TypeScript 初学者应该先掌握哪些特性？',
  ],
  // 策划头脑风暴类
  [
    '帮我规划一个五天四晚的旅行行程框架',
    '给个人项目起名，给我 10 个风格不同的候选',
    '制定一份每周三练的健身计划框架',
    '组织一次团队头脑风暴，议程怎么安排？',
  ],
  // 工具效率类
  [
    'Excel 里如何做动态数据图表？',
    'Git 分支管理有什么最佳实践？',
    'Markdown 表格对齐语法有哪些写法？',
    '有哪些提升终端效率的快捷键或工具？',
  ],
]

/** 从话题池抽推荐：随机 3 个种类 × 每类随机 1 条（Fisher-Yates 洗牌取前 3）。 */
function pickSuggestions(): string[] {
  const cats = [...WELCOME_TOPIC_POOL]
  for (let i = cats.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[cats[i], cats[j]] = [cats[j], cats[i]]
  }
  return cats.slice(0, 3).map((cat) => cat[Math.floor(Math.random() * cat.length)])
}

function WelcomeView() {
  const navigate = useNavigate()
  const createThread = useCreateThread()
  const { userId, openLogin } = useAuth()
  const [input, setInput] = useState('')
  // 推荐话题：每次挂载（进入欢迎页）重新抽一批， StrictMode 双挂载也只会多抽一次，无副作用
  const suggestions = useMemo(pickSuggestions, [])

  const handleWelcomeSend = (text: string) => {
    // 未登录：不提交，弹登录框（登录后再发一次即可）
    if (!userId) {
      openLogin()
      return
    }
    createThread.mutate(
      { title: '新对话' },
      {
        onSuccess: (thread) => {
          try {
            sessionStorage.setItem(`prism-draft:${thread.thread_id}`, text)
          } catch {
          }
          setInput('')
          navigate(`/chats/${thread.thread_id}`)
        },
        onError: (err) =>
          toast.error('新建会话失败', {
            description: err instanceof Error ? err.message : '未知错误',
          }),
      },
    )
  }

  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 px-6">
      <div className="flex flex-col items-center gap-4 text-center animate-message-in">
        <div className="relative">
          <div className="absolute -inset-3 rounded-[1.6rem] bg-primary/10 blur-xl" aria-hidden />
          <div className="relative flex size-14 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lg">
            <Sparkles className="size-7" />
          </div>
        </div>
        <div className="space-y-1.5">
          <h1 className="text-2xl font-semibold tracking-tight">欢迎使用 PrismWorker</h1>
          <p className="text-sm text-muted-foreground">
            多智能体工作流引擎 · 提问、思考、交付，一气呵成
          </p>
        </div>
      </div>

      <div className="w-full max-w-xl">
        <ChatInput
          value={input}
          onChange={setInput}
          onSend={handleWelcomeSend}
          disabled={createThread.isPending}
          placeholder="问点什么…（Enter 发送，Shift+Enter 换行）"
        />
      </div>

      <div className="flex w-full max-w-xl flex-wrap items-center justify-center gap-2">
        {suggestions.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => handleWelcomeSend(s)}
            disabled={createThread.isPending}
            className="max-w-full truncate rounded-full border bg-card px-3.5 py-1.5 text-xs text-muted-foreground transition-all hover:border-primary/30 hover:bg-accent hover:text-foreground disabled:opacity-50"
            title={s}
          >
            {s}
          </button>
        ))}
      </div>

      <p className="max-w-md text-center text-xs text-muted-foreground/70">
        也可以从左侧选择一个已有会话继续，或点击「新建会话」。
      </p>
    </div>
  )
}

function ThreadView({ threadId }: { threadId: string }) {
  const qc = useQueryClient()
  const { userId, openLogin } = useAuth()
  // 对话区滚动容器：TurnIndex 需要它做轮错定位/高亮的测量与滚动
  const scrollRef = useRef<HTMLDivElement>(null)

  const { data: threads, isLoading } = useThreads()
  const thread = threads?.find((t) => t.thread_id === threadId)

  const { data: messages = [], isLoading: msgLoading } = useMessages(threadId)

  const { data: runs } = useThreadRuns(threadId)
  const resumeRun = useMemo(
    () => runs?.find((r) => r.status === 'running' || r.status === 'pending') ?? null,
    [runs],
  )

  // 历史思考链：重开会话时回放已落库 run 的事件流（实时当前 run 由 streamState 承担）
  const { data: chains } = useThreadChains(threadId)
  const chainSnapshots = useMemo(
    () =>
      (chains ?? []).map((c) =>
        replayChainEvents(c.events, { startedAt: c.started_at, finishedAt: c.finished_at }),
      ),
    [chains],
  )

  const runStream = useRunStream(threadId, { resumeRun })
  const { state: streamState } = runStream
  const runActive = streamState.status === 'connecting' || streamState.status === 'running'

  // 模型选择已改为后端动态路由（harness/models/routing）：
  // 前端不再传 model_name，交给后端按「显式指定 → 视觉 → 关键词 → 默认」的规则决策。
  // 这里只保留思考开关（它是用户可感知、且与路由无关的偏好）。
  const [thinking, setThinking] = useState<boolean>(() => loadLocal('prism-thinking', '0') === '1')

  const activity = useMemo<RunActivity | null>(() => {
    if (streamState.status === 'idle') return null
    return {
      steps: streamState.steps,
      active: runActive,
      startedAt: streamState.startedAt,
      finishedAt: streamState.finishedAt,
      degraded: streamState.thinkingDegraded,
      // 后端 run_meta 下发的路由决策：让用户看到「这次用的是哪个模型、为什么」
      routing: streamState.routing,
    }
  }, [
    streamState.status,
    streamState.steps,
    streamState.startedAt,
    streamState.finishedAt,
    streamState.thinkingDegraded,
    streamState.routing,
    runActive,
  ])

  /**
   * 当前会话的产物路径。
   *
   * 语义要点（见 harness/agents/thread_state.py 的 merge_artifacts）：
   *   后端 ThreadState.artifacts 是**线程级累积**的（list(dict.fromkeys(existing + new))），
   *   不是按 run 重置。所以这里取的是「整个会话的产物集合」，
   *   右侧侧边栏也就该按会话维度展示，而不是只显示最近一轮。
   *
   * 优先级：
   *   1. 流进行中/刚结束 → 用 streamState.artifacts（含实时新产物，最及时）
   *   2. 冷启动（status=idle）→ 回落到已落库 run 里产物最多的那条
   * 如果将来后端补上「按线程拉取全部产物」的接口，这里替换成那个查询即可。
   */
  const artifactPaths = useMemo(() => {
    if (streamState.status === 'idle' && streamState.artifacts.length === 0) {
      const fromRuns = (runs ?? []).flatMap((r) => r.artifacts)
      return fromRuns.length > 0 ? Array.from(new Set(fromRuns)) : []
    }
    return streamState.artifacts
  }, [runs, streamState.status, streamState.artifacts])

  const [attachments, setAttachments] = useState<AttachmentItem[]>([])
  const attachKeyRef = useRef(0)
  const MAX_UPLOAD_BYTES = 20 * 1024 * 1024

  const uploadFiles = useCallback(
    (files: File[]) => {
      const valid = files.filter((f) => f.size <= MAX_UPLOAD_BYTES)
      const oversized = files.filter((f) => f.size > MAX_UPLOAD_BYTES)
      if (oversized.length > 0) {
        toast.error('上传失败', {
          description: `「${oversized.map((f) => f.name).join('、')}」超过 20MB 上限`,
        })
      }
      if (valid.length === 0) return

      for (const file of valid) {
        const key = `${file.name}-${Date.now()}-${attachKeyRef.current++}`
        setAttachments((prev) => [
          ...prev,
          { key, name: file.name, size: file.size, status: 'uploading', file },
        ])
        void uploadFile(threadId, file)
          .then((up) => {
            setAttachments((prev) =>
              prev.map((it) =>
                it.key === key
                  ? {
                      ...it,
                      status: 'done',
                      name: up.filename,
                      virtualPath: up.virtual_path,
                    }
                  : it,
              ),
            )
          })
          .catch((err) => {
            setAttachments((prev) =>
              prev.map((it) =>
                it.key === key
                  ? {
                      ...it,
                      status: 'error',
                      error: err instanceof Error ? err.message : '上传失败',
                    }
                  : it,
              ),
            )
          })
      }
    },
    [threadId, MAX_UPLOAD_BYTES],
  )

  const handleRemoveAttachment = (item: AttachmentItem) => {
    setAttachments((prev) => prev.filter((it) => it.key !== item.key))
  }
  const handleRetryAttachment = (item: AttachmentItem) => {
    if (!item.file) {
      toast.error('重试失败', { description: '本地文件引用已失效，请重新选择' })
      return
    }
    setAttachments((prev) => prev.filter((it) => it.key !== item.key))
    uploadFiles([item.file])
  }

  const runStatus = useMemo<RunStatusView | null>(() => {
    const s = streamState.status
    if (s === 'finished' || s === 'error' || s === 'cancelled') {
      return {
        status: s,
        messageCount: streamState.messageCount,
        error: streamState.error,
        startedAt: streamState.startedAt,
        finishedAt: streamState.finishedAt,
      }
    }
    return null
  }, [
    streamState.status,
    streamState.messageCount,
    streamState.error,
    streamState.startedAt,
    streamState.finishedAt,
  ])

  const [draft, setDraft] = useState<string>(() => {
    try {
      const d = sessionStorage.getItem(`prism-draft:${threadId}`)
      if (d != null) sessionStorage.removeItem(`prism-draft:${threadId}`)
      return d ?? ''
    } catch {
      return ''
    }
  })
  // 本地待发 user 气泡：text 为原文，afterCount 记录发送时服务端历史的条数，
  // 清理/去重只匹配该位置之后的消息——避免「往轮发过同文本」被误判为已落库
  const [pendingUser, setPendingUser] = useState<{ text: string; afterCount: number } | null>(null)

  // 思考链打印「会话标题已生成：X」时，立即乐观更新线程列表缓存（侧边栏/顶栏标题即时刷新；
  // 后端 run 收尾也会把标题持久化，invalidate 重拉后二者一致）
  useEffect(() => {
    const line = streamState.prints.find((p) => p.startsWith('会话标题已生成：'))
    if (!line) return
    const generated = line.slice('会话标题已生成：'.length).trim()
    if (!generated) return
    qc.setQueriesData<ThreadOut[]>({ queryKey: threadListKey }, (old) =>
      old?.map((t) => (t.thread_id === threadId ? { ...t, title: generated } : t)),
    )
  }, [streamState.prints, threadId, qc])

  // 历史一旦已包含该用户消息（流结束重拉完成），立即清理本地待发消息，避免重复展示。
  // 只匹配 afterCount 之后的位置：往轮同文本不算数（防提前清理导致本轮消息隐身）。
  // 后端压缩归档（archived_messages）保证 user 消息永不从展示历史丢失，
  // 该清理因此总能随重拉完成，不会再出现「消息被压缩删了、本地气泡吊在末尾」。
  useEffect(() => {
    if (
      pendingUser &&
      messages.some(
        (m, i) =>
          i >= pendingUser.afterCount &&
          m.role === 'user' &&
          normalizeUserContent(m.content) === pendingUser.text,
      )
    ) {
      setPendingUser(null)
    }
  }, [messages, pendingUser])

  const liveMessages = useMemo<MessageOut[]>(() => {
    const history = messages
    const list: MessageOut[] = [...history]

    // 用户消息：历史已持久化同内容则不再追加（流结束后由重拉结果承担展示）
    if (
      pendingUser &&
      !history.some(
        (m, i) =>
          i >= pendingUser.afterCount &&
          m.role === 'user' &&
          normalizeUserContent(m.content) === pendingUser.text,
      )
    ) {
      list.push({ role: 'user', content: pendingUser.text })
    }

    // 助手回复：仅当历史尚未包含同内容回复时才叠加 aiText，
    // 运行中负责流式展示，结束后作为「重拉完成前」的过渡兜底。
    //
    // 关键：结束后不要用 `content !== aiText` 这种严格相等去决定是否叠加。
    // 历史文案可能因为防注入标记剥离 / <finish/> 摘除 / 空白归一 而与 aiText
    // 存在细微差异，一旦判定「不相等」就会把整段答复再 push 一份，
    // 而重拉（invalidateQueries）回来后又消失 —— 用户看到的就是
    // 「文字突然闪一下 / 重复一小段」。这里改用「历史里最后一条 assistant
    // 是否已经覆盖了本轮答复」的宽松判定：只要历史里存在一条 assistant 且
    // 其尾部与 aiText 尾部一致，就认为历史已接管，不再叠加。
    const aiText = streamState.aiText
    if (aiText) {
      if (runActive) {
        list.push({ role: 'assistant', content: aiText })
      } else if (streamState.status === 'finished') {
        const lastAssistant = [...history].reverse().find((m) => m.role === 'assistant')
        if (!lastAssistant || !historyCoversAnswer(lastAssistant.content, aiText)) {
          list.push({ role: 'assistant', content: aiText })
        }
      }
    }
    return list
  }, [messages, pendingUser, runActive, streamState.aiText, streamState.status])

  // 轮次总数 = 用户消息数（与 MessageBubble 的 data-turn 锚点一一对应）
  const turnCount = useMemo(
    () => liveMessages.reduce((n, m) => (m.role === 'user' ? n + 1 : n), 0),
    [liveMessages],
  )

  useEffect(() => {
    if (streamState.justFinished && streamState.status === 'error' && streamState.error) {
      toast.error('运行失败', { description: streamState.error })
    }
  }, [streamState.justFinished, streamState.status, streamState.error])

  const handleThinkingChange = (v: boolean) => {
    setThinking(v)
    saveLocal('prism-thinking', v ? '1' : '0')
  }

  const handleSend = async (text: string) => {
    // 未登录：不提交，弹登录框
    if (!userId) {
      openLogin()
      return
    }
    setDraft('')
    setPendingUser({ text, afterCount: messages.length })
    // 新一轮开始前失效历史链：让已完成的往轮链从后端取回（当前轮仍走实时流）
    qc.invalidateQueries({ queryKey: chainListKey(threadId) })
    const ok = await runStream.submit(text, {
      // model_name 传 null = 交给后端动态路由决定（harness/models/routing）
      model_name: null,
      thinking_enabled: thinking,
    })
    if (!ok) setPendingUser(null)
  }
  const handleStop = () => {
    void runStream.stop()
  }

  return (
    // ArtifactsProvider 包住整页：消息流里的产物卡片（深层子树）要能唤起右侧侧边栏
    <ArtifactsProvider threadId={threadId} artifacts={artifactPaths}>
      <div
        className="flex h-full min-h-0"
        onDragOver={(e) => {
          e.preventDefault()
        }}
        onDrop={(e) => {
          e.preventDefault()
          if (e.dataTransfer?.files?.length) uploadFiles(Array.from(e.dataTransfer.files))
        }}
      >
        {/* 左：对话列（原有的纵向布局整体搬进来，结构不变） */}
        <div className="flex h-full min-w-0 flex-1 flex-col">
          <ChatHeader
            title={thread?.title}
            loading={isLoading}
            updatedAt={thread?.updated_at}
            active={runActive}
          />

          <div className="relative min-h-0 flex-1">
            <div ref={scrollRef} className="h-full overflow-y-auto">
              <MessageList
                messages={liveMessages}
                isLoading={msgLoading}
                streaming={runActive}
                activity={activity}
                runStatus={runStatus}
                artifacts={artifactPaths}
                threadId={threadId}
                chains={chainSnapshots}
                onFillInput={setDraft}
              />
            </div>
            <TurnIndex scrollRef={scrollRef} turnCount={turnCount} />
          </div>

          {streamState.status !== 'idle' && streamState.todos.length > 0 && (
            // 与 Composer/消息列同族对齐：外层 px-7 + 内层 820px 居中
            // （此前是 px-4 全宽，比输入框宽出一截，视觉上错位）
            <div className="shrink-0 px-7">
              <div className="mx-auto w-full max-w-[820px]">
                <TodosPanel todos={streamState.todos} />
              </div>
            </div>
          )}

          <AttachmentList
            items={attachments}
            onRemove={handleRemoveAttachment}
            onRetry={handleRetryAttachment}
          />
          {/* 模板 .composer：上 14px、下 22px、左右 28px */}
          <div className="shrink-0 px-7 pt-3.5 pb-[22px]">
            <Composer
              value={draft}
              onChange={setDraft}
              onSend={handleSend}
              onStop={handleStop}
              onAttach={uploadFiles}
              isRunning={runActive}
              thinking={thinking}
              onThinkingChange={handleThinkingChange}
            />
          </div>
        </div>

        {/* 右：产物侧边栏（默认关闭，由 ArtifactsContext 控制） */}
        <ArtifactPanel threadId={threadId} />
      </div>
    </ArtifactsProvider>
  )
}

/**
 * 产物侧栏的显隐壳。
 *
 * 为什么单独抽一层：侧栏开合状态在 ArtifactsContext 里，
 * 而 ThreadView 自身不需要读它（读它会导致每次开关都重渲染整个对话区 ——
 * 消息流、思考链、Composer 全都在里面，代价不小）。
 * 把这层订阅收进一个极小壳组件，开合只重渲染这里 + 侧栏本身。
 */
function ArtifactPanel({ threadId }: { threadId: string }) {
  const { open, artifacts } = useArtifacts()
  // 没有产物时不占用横向空间（默认关闭，且无内容可看）
  if (!open || artifacts.length === 0) return null
  // 宽度由 ArtifactSidePanel 内部的 usePanelResize 以内联 style 给出
  // （可拖拽调宽 + 按工作区持久化），这里不再写任何宽度类，避免与内联值打架。
  return <ArtifactSidePanel threadId={threadId} />
}