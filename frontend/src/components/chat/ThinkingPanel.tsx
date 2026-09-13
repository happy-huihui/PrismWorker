import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Bot,
  Brain,
  Check,
  ChevronDown,
  FileText,
  Files,
  FolderOpen,
  Globe,
  HardDrive,
  Link2,
  Loader2,
  PenSquare,
  Save,
  Search,
  Terminal,
  Users,
  Wrench,
  type LucideIcon,
} from 'lucide-react'

import { type ToolCallItem } from '@/core/runs/useRunStream'
import { cn } from '@/lib/utils'

interface ThinkingPanelProps {
  prints: string[]
  /** 本轮 run 的工具调用（tool_start/tool_end 累积） */
  toolCalls: ToolCallItem[]
  /** 模型思考叙述累积（thinking_chunk → 思考链文本行，DeerFlow 叙述行） */
  thinkingText?: string
  /** 本轮 run 是否仍在进行（推进自动展开 / 结束后延迟收起） */
  active: boolean
  /** run 开始时间（秒时间戳），流式中用于实时计时 */
  startedAt?: number | null
  /** run 结束时间（秒时间戳），结束后用于摘要 */
  finishedAt?: number | null
}

const TOOL_TITLES: Array<[string[], string]> = [
  [['list_uploaded_files'], '列出上传文件'],
  [['present_files'], '展示产物文件'],
  [['read_file', 'get_file'], '读取文件'],
  [['write_file', 'edit_file', 'patch_file', 'update_file'], '写入文件'],
  [['exec_command', 'run_command', 'exec'], '执行命令'],
  [['web_search', 'search'], '网络搜索'],
  [['web_fetch', 'fetch_url', 'browse', 'get_url'], '抓取网页'],
  [['task', 'spawn_task', 'delegate', 'sub_agent'], '派发子代理'],
  [['memory_save', 'save_memory', 'remember'], '保存记忆'],
  [['ask_clarification', 'dict_tool', 'request_input'], '请求澄清'],
  [['save', 'persist'], '保存内容'],
]

function toolTitle(tool: string): string {
  for (const [keys, title] of TOOL_TITLES) {
    if (keys.some((k) => tool.includes(k))) return title
  }
  return tool
}

const TOOL_ICONS: Array<[string[], LucideIcon]> = [
  [['present_files', 'list_files'], FolderOpen],
  [['read_file', 'get_file'], FileText],
  [['write_file', 'edit_file', 'patch_file', 'update_file'], PenSquare],
  [['exec_command', 'run_command', 'exec'], Terminal],
  [['web_search', 'search'], Search],
  [['web_fetch', 'fetch_url', 'browse', 'get_url'], Link2],
  [['task', 'spawn_task', 'delegate', 'sub_agent'], Users],
  [['memory_save', 'save_memory', 'remember'], HardDrive],
  [['list_uploaded_files', 'list_files'], Files],
  [['dict_tool', 'ask_user', 'request_input'], Bot],
  [['save', 'persist'], Save],
  [['web'], Globe],
]
const FALLBACK_ICON = Wrench

function toolIcon(tool: string): LucideIcon {
  for (const [keys, icon] of TOOL_ICONS) {
    if (keys.some((k) => tool.includes(k))) return icon
  }
  return FALLBACK_ICON
}

function fmtDuration(d: number | null): string {
  if (d == null) return ''
  return d < 60 ? `${d.toFixed(1)}s` : `${Math.floor(d / 60)}m${(d % 60).toFixed(0)}s`
}

type TimelineEntry =
  | { kind: 'text'; id: number; text: string }
  | { kind: 'tool'; id: number; item: ToolCallItem }

function splitThinking(text: string): string[] {
  if (!text) return []
  return text
    .split(/(?<=[。！？；!?;])\s*|\n+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

function buildTimeline(
  prints: string[],
  toolCalls: ToolCallItem[],
  thinkingText: string,
): TimelineEntry[] {
  const entries: TimelineEntry[] = []
  const taken = new Set<number>()
  const thinking = splitThinking(thinkingText)
  let thinkingIdx = 0
  let id = 0

  const pushNextThinking = () => {
    if (thinkingIdx < thinking.length) {
      entries.push({ kind: 'text', id: id++, text: thinking[thinkingIdx++] })
    }
  }

  for (const line of prints) {
    if (/本轮模型用量/.test(line)) continue
    const start = line.match(/即将调用工具\s+([\w_]+)/)
    if (start) {
      const idx = toolCalls.findIndex((t, i) => !taken.has(i) && start[1] === t.tool)
      if (idx >= 0) {
        taken.add(idx)
        pushNextThinking()
        entries.push({ kind: 'tool', id: id++, item: toolCalls[idx] })
        continue
      }
    }
    if (/工具\s+[\w_]+\s+执行完成/.test(line)) continue
    entries.push({ kind: 'text', id: id++, text: line })
  }

  toolCalls.forEach((item, i) => {
    if (!taken.has(i)) {
      pushNextThinking()
      entries.push({ kind: 'tool', id: id++, item })
    }
  })
  pushNextThinking()
  return entries
}

function useLiveElapsed(startedAt: number | null, running: boolean) {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!running || startedAt == null) return
    const tick = () => setElapsed(Math.max(0, Math.floor(Date.now() / 1000 - startedAt)))
    tick()
    const id = window.setInterval(tick, 1000)
    return () => window.clearInterval(id)
  }, [running, startedAt])
  return elapsed
}

export function ThinkingPanel({
  prints,
  toolCalls,
  thinkingText,
  active,
  startedAt,
  finishedAt,
}: ThinkingPanelProps) {
  const [open, setOpen] = useState(false)
  /** 用户是否手动操作过折叠（手动后不再自动收起，尊重用户意图） */
  const userTouchedRef = useRef(false)
  const tailRef = useRef<HTMLDivElement>(null)

  const timeline = useMemo(
    () => buildTimeline(prints, toolCalls, thinkingText ?? ''),
    [prints, toolCalls, thinkingText],
  )

  const doneDuration = useMemo(() => {
    if (finishedAt != null && startedAt != null) {
      return Math.max(0, Math.floor(finishedAt - startedAt))
    }
    return undefined
  }, [finishedAt, startedAt])

  // 进行中自动展开；结束后若用户未手动操作过，先保持展示几秒再收起，
  // 让「思考过程 → 回答」的衔接自然不突兀
  useEffect(() => {
    if (active) {
      setOpen(true)
      return
    }
    if (userTouchedRef.current) return
    const t = window.setTimeout(() => setOpen(false), 3500)
    return () => window.clearTimeout(t)
  }, [active])

  useEffect(() => {
    if (open && active) {
      tailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [timeline.length, open, active])

  const liveElapsed = useLiveElapsed(startedAt ?? null, active)

  const handleToggle = () => {
    userTouchedRef.current = true
    setOpen((v) => !v)
  }

  if (timeline.length === 0) return null

  return (
    <div className="my-1 overflow-hidden rounded-xl border bg-muted/40 transition-colors">
      <button
        type="button"
        onClick={handleToggle}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/60"
      >
        <ChevronDown
          className={cn('size-3.5 shrink-0 transition-transform', !open && '-rotate-90')}
        />
        <Brain className="size-3.5 shrink-0" />
        <span className="font-medium">思考过程</span>
        {timeline.length > 0 && (
          <span className="text-muted-foreground/60">{timeline.length} 步</span>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          {active ? (
            <>
              <Loader2 className="size-3 animate-spin text-sky-500" />
              <span className="text-sky-500">思考中… ({liveElapsed}s)</span>
            </>
          ) : doneDuration != null ? (
            <span className="text-muted-foreground/60">耗时 {doneDuration}s</span>
          ) : null}
        </span>
      </button>

      {open && (
        <div className="border-t border-border/60 px-3 pt-2.5 pb-1">
          <div className="flex flex-col">
            {timeline.map((entry, i) => {
              const isLast = i === timeline.length - 1
              const hasConnector = !(isLast && active)
              return (
                <div key={entry.id} className="animate-step-in flex gap-2.5">
                  <div className="relative mt-1 flex shrink-0 flex-col items-center">
                    {entry.kind === 'text' ? (
                      <span className="mt-0.5 size-1.5 rounded-full bg-muted-foreground/50" />
                    ) : (
                      <ToolIconCircle icon={toolIcon(entry.item.tool)} running={entry.item.status === 'running'} />
                    )}
                    {hasConnector && <span aria-hidden className="w-px flex-1 bg-border" />}
                  </div>

                  <div className="min-w-0 flex-1 pb-2.5">
                    {entry.kind === 'text' ? (
                      <p className="text-xs leading-relaxed text-muted-foreground">
                        <span className="whitespace-pre-wrap break-words">{entry.text}</span>
                      </p>
                    ) : (
                      <ToolCallRowContent item={entry.item} />
                    )}
                  </div>
                </div>
              )
            })}

            {active && (
              <div className="animate-step-in flex gap-2.5">
                <div className="mt-0.5 flex shrink-0 flex-col items-center">
                  <span className="flex size-5 items-center justify-center rounded-full bg-sky-500/10 ring-1 ring-sky-500/30">
                    <Loader2 className="size-3 animate-spin text-sky-500" />
                  </span>
                </div>
                <div className="flex min-w-0 flex-1 items-center gap-2 pb-0.5 text-xs text-sky-500">
                  <span>正在思考…</span>
                  <span className="animate-pulse">……</span>
                </div>
              </div>
            )}
            <div ref={tailRef} aria-hidden />
          </div>
        </div>
      )}
    </div>
  )
}

function ToolIconCircle({ icon: Icon, running }: { icon: LucideIcon; running: boolean }) {
  return (
    <span
      className={cn(
        'flex size-5 items-center justify-center rounded-full ring-1',
        running ? 'bg-sky-500/10 ring-sky-500/30' : 'bg-background ring-border',
      )}
    >
      <Icon className={cn('size-3', running ? 'text-sky-500' : 'text-muted-foreground')} />
    </span>
  )
}

function ToolCallRowContent({ item }: { item: ToolCallItem }) {
  const Icon = toolIcon(item.tool)
  const running = item.status === 'running'
  const failed = item.status === 'failed'

  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1.5 text-xs">
        <Icon className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate font-medium text-foreground">{toolTitle(item.tool)}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1">
          {running ? (
            <>
              <Loader2 className="size-3 animate-spin text-sky-500" />
              <span className="text-sky-500">进行中</span>
            </>
          ) : failed ? (
            <span className="text-red-500">失败</span>
          ) : item.duration_seconds != null ? (
            <>
              <Check className="size-3 text-emerald-500" />
              <span className="text-emerald-600 dark:text-emerald-400">
                {fmtDuration(item.duration_seconds)}
              </span>
            </>
          ) : (
            <>
              <Check className="size-3 text-emerald-500" />
              <span className="text-emerald-600 dark:text-emerald-400">完成</span>
            </>
          )}
        </span>
      </div>

      {item.args_preview && (
        <pre className="mt-1 max-h-20 overflow-y-auto rounded-md bg-muted/70 px-2 py-1.5 text-[11px] leading-relaxed text-muted-foreground">
          <code className="whitespace-pre-wrap break-all font-mono">{item.args_preview}</code>
        </pre>
      )}
    </div>
  )
}