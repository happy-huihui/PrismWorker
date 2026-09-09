import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, Sparkles } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
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
import { useCreateThread, useThreads } from '@/core/threads'
import { useRunStream, useThreadRuns } from '@/core/runs'
import { uploadFile } from '@/core/uploads'
import { type MessageOut } from '@/core/api/types'

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
    <div className="flex h-full flex-col items-center justify-center gap-5 px-6">
      <div className="flex flex-col items-center gap-4 text-center">
        <div className="flex size-14 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lg">
          <Sparkles className="size-7" />
        </div>
        <div className="space-y-1.5">
          <h1 className="text-2xl font-semibold tracking-tight">欢迎使用 PrismWorker</h1>
          <p className="text-sm text-muted-foreground">
            多智能体工作流引擎 · 输入问题即可开始对话
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

      <p className="max-w-md text-center text-xs text-muted-foreground/80">
        也可以从左侧选择一个已有会话继续，或点击「新建会话」。
      </p>
    </div>
  )
}

function ThreadView({ threadId }: { threadId: string }) {
  const navigate = useNavigate()

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

  const liveMessages = useMemo<MessageOut[]>(() => {
    const list: MessageOut[] = [...messages]
    if (pendingUser) list.push({ role: 'user', content: pendingUser })
    if ((runActive && streamState.aiText) || (!runActive && streamState.aiText && streamState.status === 'finished')) {
      list.push({ role: 'assistant', content: streamState.aiText })
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
      <header className="flex h-14 shrink-0 items-center gap-2 border-b px-3">
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