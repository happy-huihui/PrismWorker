import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, Sparkles } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ChatInput } from '@/components/chat/ChatInput'
import { MessageList, type RunActivity, type RunStatusView } from '@/components/chat/MessageList'
import { ModelSelector } from '@/components/chat/ModelSelector'
import { ThinkingToggle } from '@/components/chat/ThinkingToggle'
import { TodosPanel } from '@/components/chat/TodosPanel'
import { AttachmentList, type AttachmentItem } from '@/components/chat/AttachmentList'
import { ArtifactPreviewDrawer } from '@/components/artifacts/ArtifactPreviewDrawer'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useModels } from '@/core/models'
import { useMessages } from '@/core/messages'
import { threadListKey, useCreateThread, useThreads } from '@/core/threads'
import { useRunStream, useThreadRuns } from '@/core/runs'
import { uploadFile } from '@/core/uploads'
import { type MessageOut, type ThreadOut } from '@/core/api/types'

export function ChatPage() {
  const { threadId } = useParams()
  return threadId ? <ThreadView threadId={threadId} /> : <WelcomeView />
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

const WELCOME_SUGGESTIONS = [
  '用通俗易懂的方式给我讲解堆排序',
  '帮我写一份项目周报的结构大纲',
  '如何理解 React 的 useMemo 与 useCallback？',
]

function WelcomeView() {
  const navigate = useNavigate()
  const createThread = useCreateThread()
  const [input, setInput] = useState('')

  const handleWelcomeSend = (text: string) => {
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
        {WELCOME_SUGGESTIONS.map((s) => (
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
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data: threads, isLoading } = useThreads()
  const thread = threads?.find((t) => t.thread_id === threadId)

  const { data: messages = [], isLoading: msgLoading } = useMessages(threadId)

  const { data: runs } = useThreadRuns(threadId)
  const resumeRun = useMemo(
    () => runs?.find((r) => r.status === 'running' || r.status === 'pending') ?? null,
    [runs],
  )

  const runStream = useRunStream(threadId, { resumeRun })
  const { state: streamState } = runStream
  const runActive = streamState.status === 'connecting' || streamState.status === 'running'

  const { data: models } = useModels()
  const [modelName, setModelName] = useState<string | null>(() => loadLocal('prism-model', ''))
  const [thinking, setThinking] = useState<boolean>(() => loadLocal('prism-thinking', '0') === '1')
  const currentModel = models?.find((m) => m.name === modelName)

  const activity = useMemo<RunActivity | null>(() => {
    if (streamState.status === 'idle') return null
    return {
      prints: streamState.prints,
      toolCalls: streamState.toolCalls,
      thinkingText: streamState.thinkingText,
      active: runActive,
      startedAt: streamState.startedAt,
      finishedAt: streamState.finishedAt,
    }
  }, [
    streamState.status,
    streamState.prints,
    streamState.toolCalls,
    streamState.thinkingText,
    streamState.startedAt,
    streamState.finishedAt,
    runActive,
  ])

  const artifactPaths = useMemo(() => {
    if (streamState.status === 'idle' && streamState.artifacts.length === 0) {
      return runs?.find((r) => r.artifacts.length > 0)?.artifacts ?? []
    }
    return streamState.artifacts
  }, [runs, streamState.status, streamState.artifacts])
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewPath, setPreviewPath] = useState<string | null>(null)
  const handleArtifactPreview = (path: string) => {
    setPreviewPath(path)
    setPreviewOpen(true)
  }

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
  const [pendingUser, setPendingUser] = useState<string | null>(null)

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

  // 历史一旦已包含该用户消息（流结束重拉完成），立即清理本地待发消息，避免重复展示
  useEffect(() => {
    if (
      pendingUser &&
      messages.some((m) => m.role === 'user' && normalizeUserContent(m.content) === pendingUser)
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
      !history.some((m) => m.role === 'user' && normalizeUserContent(m.content) === pendingUser)
    ) {
      list.push({ role: 'user', content: pendingUser })
    }

    // 助手回复：仅当历史尚未包含同内容回复时才叠加 aiText，
    // 运行中负责流式展示，结束后作为「重拉完成前」的过渡兜底；
    // 若历史最后一条 assistant 与 aiText 一致，交给历史渲染，避免重复。
    const aiText = streamState.aiText
    if (aiText) {
      if (runActive) {
        list.push({ role: 'assistant', content: aiText })
      } else if (streamState.status === 'finished') {
        const lastAssistant = [...history].reverse().find((m) => m.role === 'assistant')
        if (!lastAssistant || lastAssistant.content !== aiText) {
          list.push({ role: 'assistant', content: aiText })
        }
      }
    }
    return list
  }, [messages, pendingUser, runActive, streamState.aiText, streamState.status])

  useEffect(() => {
    if (streamState.justFinished && streamState.status === 'error' && streamState.error) {
      toast.error('运行失败', { description: streamState.error })
    }
  }, [streamState.justFinished, streamState.status, streamState.error])

  const handleModelChange = (name: string) => {
    setModelName(name)
    saveLocal('prism-model', name)
  }
  const handleThinkingChange = (v: boolean) => {
    setThinking(v)
    saveLocal('prism-thinking', v ? '1' : '0')
  }

  const handleSend = async (text: string) => {
    setDraft('')
    setPendingUser(text)
    const ok = await runStream.submit(text, {
      model_name: modelName || null,
      thinking_enabled: thinking,
    })
    if (!ok) setPendingUser(null)
  }
  const handleStop = () => {
    void runStream.stop()
  }

  return (
    <div
      className="flex h-full flex-col"
      onDragOver={(e) => {
        e.preventDefault()
      }}
      onDrop={(e) => {
        e.preventDefault()
        if (e.dataTransfer?.files?.length) uploadFiles(Array.from(e.dataTransfer.files))
      }}
    >
      <header className="flex h-14 shrink-0 items-center gap-2 border-b bg-background/80 px-3 backdrop-blur-sm">
        <Button
          variant="ghost"
          size="icon"
          className="size-8 shrink-0"
          onClick={() => navigate('/')}
          title="返回"
        >
          <ArrowLeft className="size-4" />
        </Button>

        <div className="flex min-w-0 flex-1 items-center gap-2">
          {isLoading ? (
            <Skeleton className="h-5 w-40" />
          ) : (
            <>
              <h2 className="truncate text-sm font-semibold">{thread?.title ?? '会话'}</h2>
              {runActive && (
                <Badge variant="secondary" className="shrink-0 text-xs">
                  运行中
                </Badge>
              )}
            </>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <ModelSelector value={modelName} onChange={handleModelChange} disabled={runActive} />
          <ThinkingToggle
            enabled={thinking}
            onChange={handleThinkingChange}
            supported={currentModel?.supports_thinking ?? true}
            disabled={runActive}
          />
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <MessageList
          messages={liveMessages}
          isLoading={msgLoading}
          streaming={runActive}
          activity={activity}
          runStatus={runStatus}
          artifacts={artifactPaths}
          onArtifactPreview={handleArtifactPreview}
          threadId={threadId}
        />
      </div>

      <ArtifactPreviewDrawer
        threadId={threadId}
        path={previewPath}
        open={previewOpen}
        onOpenChange={setPreviewOpen}
      />

      {streamState.status !== 'idle' && streamState.todos.length > 0 && (
        <div className="shrink-0 px-4">
          <TodosPanel todos={streamState.todos} />
        </div>
      )}

      <AttachmentList
        items={attachments}
        onRemove={handleRemoveAttachment}
        onRetry={handleRetryAttachment}
      />

      <div className="shrink-0 p-3 pt-0 sm:p-4 sm:pt-0">
        <ChatInput
          value={draft}
          onChange={setDraft}
          onSend={handleSend}
          onStop={handleStop}
          onAttach={uploadFiles}
          isRunning={runActive}
        />
      </div>
    </div>
  )
}