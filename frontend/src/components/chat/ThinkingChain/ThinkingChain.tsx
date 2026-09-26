import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, MorphIcon, Route, ThinkAtom } from '@/components/icons'
import { type ChainStep, type RoutingInfo } from '@/core/runs/useRunStream'
import { formatDuration } from '@/lib/format'
import { cn } from '@/lib/utils'

import { TimelineItem } from './TimelineItem'

/**
 * 思考链容器（ThinkingChain）
 *
 * 职责：把一次 run 的过程步骤（模型思考 reasoning / 本轮叙述 narration / 工具调用 tool）
 *      渲染成「思考过程」卡片，形态对齐 DeerFlow 的 ChainOfThought：
 *      头部 = atom 小方标 + 标题 + 步数，右侧 mono 计时/耗时；
 *      正文 = 有序时间线。
 *
 * 结束后的行为（对齐 DeerFlow，修复「思考链直接没了」）：
 *      DeerFlow 的处理是 —— 面板本身**始终保留在对话里**（`open={true}`），
 *      只是把「最后一次工具调用之前的历史步骤」折叠起来，给一个
 *      「查看其他 N 个步骤」的按钮，点开变「隐藏步骤」。
 *      所以用户永远不会觉得思考链消失，随时可以回看。
 *      这里沿用同样的思路：
 *        - 进行中：全部步骤展开 + 跟随滚到底；
 *        - 结束后：头部与最后一步保留，更早的步骤折叠到「查看其他 N 个步骤」；
 *        - 用户手动展开/收起后尊重用户意图（不自动覆盖）。
 *
 * 占位：`pending`（已回车但还没有任何步骤）时也要出卡片，显示「正在处理…」——
 *      后端装配 + 模型首字可能几秒，这段对等时间不能是白屏。
 */
interface ThinkingChainProps {
  /** 时间线步骤（按后端事件到达顺序，同一条消息的思考/叙述会原地增长） */
  steps: ChainStep[]
  /** 本轮是否仍在进行（驱动自动展开/计时/末尾"正在思考"） */
  active: boolean
  startedAt?: number | null
  finishedAt?: number | null
  /** 请求了思考但模型不支持，已被降级（卡片里说明一句，不拿错误打断会话） */
  degraded?: boolean
  /** 尚无步骤的进行中状态：出占位卡（正在处理…） */
  pending?: boolean
  /** 后端动态路由的决策结果（模型名 + 决策来源，鼠标悬停可看理由） */
  routing?: RoutingInfo | null
}

/** 结束后默认折叠的"尾部保留步数"：最后 N 步始终可见（对齐 DeerFlow 保留最后一次工具调用） */
const TAIL_KEEP = 2

/** 进行中每秒刷新已用时间。 */
function useLiveElapsed(startedAt: number | null | undefined, running: boolean): number {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!running || startedAt == null) return
    const tick = () => setElapsed(Math.max(0, Math.floor(Date.now() / 1000 - startedAt)))
    tick()
    const id = window.setInterval(tick, 1000)
    return () => window.clearInterval(id)
  }, [startedAt, running])
  return elapsed
}

export function ThinkingChain({
  steps,
  active,
  startedAt,
  finishedAt,
  degraded,
  pending,
  routing,
}: ThinkingChainProps) {
  // 是否展开"更早的历史步骤"（DeerFlow 的 showAbove）
  const [showAll, setShowAll] = useState(false)
  // 用户是否手动操作过（手动后不再自动改状态，尊重用户意图）
  const userTouchedRef = useRef(false)
  const tailRef = useRef<HTMLDivElement>(null)

  // 1.结束后的静态耗时
  const doneDuration = (() => {
    if (finishedAt != null && startedAt != null) return Math.max(0, Math.floor(finishedAt - startedAt))
    return undefined
  })()

  // 2.进行中总是展开（让用户看到实时过程）；一旦结束则收起为"尾部 + 查看其他 N 个步骤"
  //   （对齐 DeerFlow：面板不消失，但默认只留最后一次工具调用附近的步骤）
  useEffect(() => {
    if (active) {
      // 新一轮开始：重置为展开且清掉"用户已操作"标记
      userTouchedRef.current = false
      setShowAll(true)
      return
    }
    // 结束：若用户中途没手动操作过，就自动收起到尾部视图
    if (!userTouchedRef.current) setShowAll(false)
  }, [active])

  // 3.进行中且展开时，跟随滚到底
  useEffect(() => {
    if (showAll && active) tailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [steps.length, showAll, active])

  const liveElapsed = useLiveElapsed(startedAt ?? null, active)

  const handleToggleAll = () => {
    userTouchedRef.current = true
    setShowAll((v) => !v)
  }

  // 折叠时：保留最后 TAIL_KEEP 步可见，更早的收进「查看其他 N 个步骤」
  const hiddenCount = useMemo(
    () => (showAll ? 0 : Math.max(0, steps.length - TAIL_KEEP)),
    [showAll, steps.length],
  )
  const visibleSteps = useMemo(
    () => (hiddenCount > 0 ? steps.slice(hiddenCount) : steps),
    [hiddenCount, steps],
  )

  // 4.既没有步骤、也不是进行中 → 本轮没有可展示的过程（不渲染空壳）
  if (steps.length === 0 && !pending) return null

  return (
    <div className="overflow-hidden rounded-[14px] border bg-card shadow-sm">
      {/* 头部：atom 软底小方标 + 标题 + 步数 + 右侧 mono 计时（模板 .think .h） */}
      <button
        type="button"
        onClick={handleToggleAll}
        className="flex w-full items-center gap-2.5 px-4 py-3 text-left text-[13px] text-muted-foreground transition-colors hover:bg-accent/40"
      >
        <span className="grid size-[22px] shrink-0 place-items-center rounded-[7px] bg-primary/10 text-primary">
          <MorphIcon icon={ThinkAtom} className="size-3.5" />
        </span>
        <b className="shrink-0 text-[13px] font-semibold text-foreground">思考过程</b>
        {steps.length > 0 && <span>· {steps.length} 步</span>}

        {/* 后端动态路由的决策：模型由规则自动选定，这里如实告知用户选了哪个
            （title 里给完整理由，避免把一长串解释塞进卡片头部） */}
        {routing && (
          <span
            className={cn(
              'hidden shrink-0 items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] sm:inline-flex',
              routing.escalated
                ? 'border-primary/30 bg-primary/10 text-primary'
                : 'border-border bg-muted/60 text-muted-foreground',
            )}
            title={routing.reason}
          >
            <Route className="size-2.5" />
            {routing.modelName}
          </span>
        )}

        <span className="ml-auto shrink-0 font-mono text-xs">
          {active ? (
            <span className="text-primary">
              {steps.length === 0
                ? `正在处理…（${formatDuration(liveElapsed)}）`
                : `思考中…（${formatDuration(liveElapsed)}）`}
            </span>
          ) : doneDuration != null ? (
            <span>耗时 {formatDuration(doneDuration)}</span>
          ) : null}
        </span>
        <ChevronDown
          className={cn(
            'size-3.5 shrink-0 transition-transform duration-200',
            showAll ? 'rotate-180' : 'rotate-0',
          )}
        />
      </button>

      {/* 正文：时间线（折叠时先给「查看其他 N 个步骤」入口） */}
      {showAll && (
        <div className="px-4 pt-1 pb-3.5">
          <div className="flex flex-col">
            {steps.map((step, i) => (
              <TimelineItem key={step.id} step={step} isLast={i === steps.length - 1} active={active} />
            ))}

            {/* 思考被降级：一句话说明，让用户知道为什么这次没有深度思考 */}
            {degraded && (
              <p className="pb-1 pl-8 text-xs text-muted-foreground/80">
                当前模型不支持深度思考，已按常规模式作答。
              </p>
            )}

            {active && (
              <div className="flex items-center gap-2 py-1 pl-8 text-xs text-primary">
                <span>{steps.length === 0 ? '正在处理' : '正在思考'}</span>
                <span className="animate-pulse">···</span>
              </div>
            )}
            <div ref={tailRef} aria-hidden />
          </div>

          {steps.length > 0 && (
            <button
              type="button"
              onClick={handleToggleAll}
              className="ml-8 mt-0.5 self-start rounded-full border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              隐藏步骤
            </button>
          )}
        </div>
      )}

      {/* 折叠态：只留最后几步 + 「查看其他 N 个步骤」入口（对齐 DeerFlow moreSteps） */}
      {!showAll && (
        <div className="px-4 pt-1 pb-3.5">
          {hiddenCount > 0 && (
            <button
              type="button"
              onClick={handleToggleAll}
              className="mb-1.5 flex w-full items-center gap-2 rounded-md py-1 pl-8 text-left text-xs text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
            >
              <ChevronDown className="size-3.5 shrink-0 rotate-180 opacity-70" />
              <span>查看其他 {hiddenCount} 个步骤</span>
            </button>
          )}
          <div className="flex flex-col">
            {visibleSteps.map((step, i) => (
              <TimelineItem
                key={step.id}
                step={step}
                isLast={i === visibleSteps.length - 1}
                active={active}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
