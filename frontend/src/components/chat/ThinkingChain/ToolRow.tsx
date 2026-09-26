import { MorphIcon, resolveToolIcon } from '@/components/icons'
import { type ToolCallItem } from '@/core/runs/useRunStream'
import { cn } from '@/lib/utils'

import { isCommandTool, isPathTarget, stepTitle, toolTarget } from './buildTimeline'

/**
 * 工具行（ToolRow）
 *
 * 职责：思考链里一次工具调用的展示行——对齐 DeerFlow 的 `ChainOfThoughtStep`：
 *      语义描边图标（17px，随主题着色）+ 动作标题（模型自填 description 优先）
 *      + 右侧状态「完成 · 0.4s」；
 *      下方附一枚「目标」chip（路径 / 关键词，mono 等宽），
 *      命令类工具（exec_command 等）改用代码块样式呈现，贴近 DeerFlow 的 bash 卡片。
 */

/** 秒 → 人话时长（<60s 显示 x.xs，否则 xm y s）。 */
function fmtDuration(d: number | null): string {
  if (d == null) return ''
  // 工具步骤行是紧凑 mono 风格（0.5s / 2m3s），与 run 级耗时的中文分段不同；
  // 这里只补上小时段（长命令之前会显示成 61m5s，现在收敛成 1h1m5s）
  if (d < 60) return `${d.toFixed(1)}s`
  const total = Math.floor(d)
  const hour = Math.floor(total / 3600)
  const min = Math.floor((total % 3600) / 60)
  const sec = total % 60
  if (hour > 0) return `${hour}h${min}m${sec}s`
  return `${min}m${sec}s`
}

export function ToolRow({ item }: { item: ToolCallItem }) {
  const running = item.status === 'running'
  const failed = item.status === 'failed'
  const target = toolTarget(item)
  const isCommand = isCommandTool(item.tool) && !!target
  // 非命令类但有目标：路径类用等宽 chip，其它（关键词/URL）也用等宽但更紧凑
  const asPathChip = !isCommand && !!target && isPathTarget(item)

  return (
    <div className="min-w-0">
      {/* 1.图标 + 标题 + 状态（对齐 DeerFlow：标题走模型自填 description） */}
      <div className="flex items-center gap-2 text-sm">
        <MorphIcon
          icon={resolveToolIcon(item.tool)}
          className={cn(
            'size-[17px] shrink-0',
            running ? 'animate-pulse text-primary' : failed ? 'text-destructive' : 'text-muted-foreground',
          )}
        />
        <span className="min-w-0 flex-1 truncate font-medium">{stepTitle(item)}</span>
        <span className="shrink-0 pl-2 text-xs">
          {running ? (
            <span className="text-primary">进行中…</span>
          ) : failed ? (
            <span className="text-destructive">失败</span>
          ) : item.duration_seconds != null ? (
            <span className="text-muted-foreground">完成 · {fmtDuration(item.duration_seconds)}</span>
          ) : (
            <span className="text-muted-foreground">完成</span>
          )}
        </span>
      </div>

      {/* 2.目标：命令走代码块（对齐 DeerFlow bash 卡片），其余走 mono chip */}
      {isCommand && (
        <pre className="mt-1.5 max-w-full overflow-x-auto rounded-lg border bg-secondary/60 px-2.5 py-1.5 font-mono text-xs leading-[1.6] text-foreground/90">
          <code className="whitespace-pre-wrap break-all">{target}</code>
        </pre>
      )}
      {!isCommand && target && (
        <code
          className={cn(
            'mt-1.5 inline-block max-w-full truncate rounded-md border bg-secondary/60 px-2 py-[3px] font-mono text-xs text-muted-foreground',
            asPathChip ? 'text-muted-foreground' : 'text-muted-foreground/90',
          )}
          title={target}
        >
          {target}
        </code>
      )}
    </div>
  )
}
