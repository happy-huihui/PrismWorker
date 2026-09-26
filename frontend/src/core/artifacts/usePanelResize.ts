import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * 产物侧栏「拖拽调宽」（usePanelResize）
 *
 * 职责：把「面板宽度」这件事收成一个 hook —— 拖拽计算、边界钳制、持久化。
 *      侧栏组件只负责把 width 套到 style 上、把 onHandlePointerDown 挂到手柄上。
 *
 * 为什么宽度用 localStorage 且**不按 threadId 分键**（与同目录 context.tsx 的
 * open / selectedPath 不同）：
 *      「栏宽」是工作区级的外观偏好，不是某个会话的属性。用户调一次宽窄，
 *      切到别的会话理应保持 —— 若按 thread 分键，每换一个会话都要重调一次，
 *      反而违背直觉。故这里用独立全局键。
 *
 * 为什么用 pointer 事件 + window 监听而不是 setPointerCapture：
 *      拖动过程中光标很容易移出手柄（甚至移出面板），只在手柄上监听会丢事件。
 *      挂到 window 上能保证「按下 → 移动 → 抬起」整条链路不中断。
 */

/** 默认宽度（px）：够放一个窄版网页预览，又不至于把对话区挤太窄 */
export const PANEL_WIDTH_DEFAULT = 420
/** 最小宽度：再窄就看不清产物内容了 */
export const PANEL_WIDTH_MIN = 320
/** 最大宽度上限（还会与视口 60% 取更小值，防止在大屏上无限拉宽） */
export const PANEL_WIDTH_MAX = 760

/** 全局持久化键（不按会话分，理由见文件头注释） */
const WIDTH_STORAGE_KEY = 'prism-artifacts:panel-width'

/** 视口约束下的实际上限：大屏也不让面板超过屏宽的 60% */
function effectiveMax(): number {
  if (typeof window === 'undefined') return PANEL_WIDTH_MAX
  return Math.min(PANEL_WIDTH_MAX, Math.round(window.innerWidth * 0.6))
}

/** 把任意数值钳到合法区间（同时兜住 NaN） */
function clampWidth(value: number): number {
  if (!Number.isFinite(value)) return PANEL_WIDTH_DEFAULT
  const max = effectiveMax()
  // 视口很窄时 max 可能小于 min，此时以 min 为准，避免出现 min>max 的空区间
  return Math.min(Math.max(Math.round(value), PANEL_WIDTH_MIN), Math.max(max, PANEL_WIDTH_MIN))
}

function readStoredWidth(): number {
  try {
    const raw = localStorage.getItem(WIDTH_STORAGE_KEY)
    if (!raw) return PANEL_WIDTH_DEFAULT
    return clampWidth(Number(raw))
  } catch {
    // localStorage 可能被禁用；读不到就用默认值，不影响功能
    return PANEL_WIDTH_DEFAULT
  }
}

export interface PanelResizeApi {
  /** 当前面板宽度（px） */
  width: number
  /** 是否正在拖拽（供手柄高亮 / 拖动时禁用过渡动画） */
  dragging: boolean
  /** 挂到拖拽手柄的 onPointerDown */
  onHandlePointerDown: (event: React.PointerEvent<HTMLElement>) => void
  /** 键盘微调宽度（方向键），让手柄对键盘用户也可用 */
  nudgeWidth: (delta: number) => void
  /** 复位到默认宽度（手柄双击 / Home 键） */
  resetWidth: () => void
}

export function usePanelResize(): PanelResizeApi {
  const [width, setWidth] = useState<number>(() => readStoredWidth())
  const [dragging, setDragging] = useState(false)
  /** 本次拖拽的起点快照：按下时的光标 X 与面板宽度 */
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null)

  // 拖拽期间把监听挂到 window：光标移出手柄也不丢事件
  useEffect(() => {
    if (!dragging) return

    const onMove = (event: PointerEvent) => {
      const start = dragRef.current
      if (!start) return
      // 面板在右侧：光标左移 = 面板变宽，所以取 startX - clientX
      const delta = start.startX - event.clientX
      setWidth(clampWidth(start.startWidth + delta))
    }
    const stop = () => {
      dragRef.current = null
      setDragging(false)
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', stop)
    window.addEventListener('pointercancel', stop)
    // 拖拽时给 body 上禁止选中，避免整页文字被划蓝
    const prevUserSelect = document.body.style.userSelect
    document.body.style.userSelect = 'none'
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', stop)
      window.removeEventListener('pointercancel', stop)
      document.body.style.userSelect = prevUserSelect
    }
  }, [dragging])

  // 宽度变化即持久化（拖拽中每帧都写，localStorage 同步写开销可忽略）
  useEffect(() => {
    try {
      localStorage.setItem(WIDTH_STORAGE_KEY, String(width))
    } catch {
      // 写失败不影响本次使用
    }
  }, [width])

  // 视口缩小后原宽度可能越界：跟随窗口尺寸重新钳一次
  useEffect(() => {
    const onResize = () => setWidth((w) => clampWidth(w))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const onHandlePointerDown = useCallback((event: React.PointerEvent<HTMLElement>) => {
    // 只响应主键拖拽，避免右键/中键误触发
    if (event.button !== 0) return
    event.preventDefault()
    dragRef.current = { startX: event.clientX, startWidth: width }
    setDragging(true)
  }, [width])

  const resetWidth = useCallback(() => setWidth(clampWidth(PANEL_WIDTH_DEFAULT)), [])

  const nudgeWidth = useCallback((delta: number) => {
    setWidth((w) => clampWidth(w + delta))
  }, [])

  return { width, dragging, onHandlePointerDown, nudgeWidth, resetWidth }
}
