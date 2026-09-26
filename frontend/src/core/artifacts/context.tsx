import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

/**
 * 产物侧边栏状态（ArtifactsContext）
 *
 * 职责：管理右侧产物预览栏的「开关 / 当前选中文件」，并做会话级持久化。
 *      参考 DeerFlow 的 ArtifactsProvider，按我方项目特点简化：
 *        - 不做编辑草稿（drafts），只做「看 + 下载」
 *        - 持久化到 sessionStorage，按 threadId 分键（DeerFlow 按 pathname）
 *        - 用户手动关过之后，本轮会话内不再自动弹开（autoOpen 语义）
 *
 * 为什么用 Context 而不是把 state 提在 ChatPage：
 *      侧边栏由 ChatPage 渲染，但「点击消息流里的产物卡片」发生在
 *      深层子树（MessageList → MessageBubble → ArtifactCardList）。
 *      一层层传 props 会污染整条消息渲染链；用 Context 让任意深度
 *      都能唤起侧边栏。
 */

const STORAGE_PREFIX = 'prism-artifacts'

interface PersistedState {
  open: boolean
  selectedPath: string | null
}

function storageKey(threadId: string): string {
  return `${STORAGE_PREFIX}:${threadId}`
}

function readPersisted(threadId: string): PersistedState | null {
  try {
    const raw = sessionStorage.getItem(storageKey(threadId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<PersistedState>
    if (typeof parsed.open !== 'boolean') return null
    return {
      open: parsed.open,
      selectedPath: typeof parsed.selectedPath === 'string' ? parsed.selectedPath : null,
    }
  } catch {
    return null
  }
}

export interface ArtifactsContextValue {
  /** 侧边栏是否展开 */
  open: boolean
  /** 当前预览的产物虚拟路径（null = 未选中任何文件） */
  selectedPath: string | null
  /** 展示某个产物：选中并展开侧边栏 */
  select: (path: string) => void
  /** 仅设置开合（用户点关闭按钮 / 手动展开） */
  setOpen: (open: boolean) => void
  /** 当前会话的全部产物路径（由页面注入，供侧边栏列表渲染） */
  artifacts: string[]
}

const ArtifactsContext = createContext<ArtifactsContextValue | null>(null)

interface ArtifactsProviderProps {
  threadId: string
  /** 当前会话的全部产物路径 */
  artifacts: string[]
  children: ReactNode
}

export function ArtifactsProvider({ threadId, artifacts, children }: ArtifactsProviderProps) {
  // 首次挂载 / 切线程时从 sessionStorage 恢复
  const [open, setOpenState] = useState(false)
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const hydratedRef = useRef<string | null>(null)

  useEffect(() => {
    if (hydratedRef.current === threadId) return
    hydratedRef.current = threadId
    const persisted = readPersisted(threadId)
    setOpenState(persisted?.open ?? false)
    setSelectedPath(persisted?.selectedPath ?? null)
  }, [threadId])

  // 变化即持久化（hydrate 之前不写，避免把默认值盖掉已存状态）
  useEffect(() => {
    if (hydratedRef.current !== threadId) return
    try {
      sessionStorage.setItem(storageKey(threadId), JSON.stringify({ open, selectedPath }))
    } catch {
      // sessionStorage 可能被禁用或写满；持久化失败不影响功能
    }
  }, [threadId, open, selectedPath])

  // 选中项若已不在产物列表里（换了会话 / 产物被清），自动清掉选中，
  // 避免侧边栏对着一个不存在的文件一直报「产物不存在」
  useEffect(() => {
    if (!selectedPath) return
    if (artifacts.length === 0) return
    if (!artifacts.includes(selectedPath)) setSelectedPath(null)
  }, [artifacts, selectedPath])

  const select = useCallback((path: string) => {
    setSelectedPath(path)
    setOpenState(true)
  }, [])

  const setOpen = useCallback((next: boolean) => {
    setOpenState(next)
  }, [])

  const value = useMemo<ArtifactsContextValue>(
    () => ({ open, selectedPath, select, setOpen, artifacts }),
    [open, selectedPath, select, setOpen, artifacts],
  )

  return <ArtifactsContext.Provider value={value}>{children}</ArtifactsContext.Provider>
}

/**
 * 取用产物侧边栏状态。
 *
 * 注意：本 hook 必须在 ArtifactsProvider 内部调用。消息渲染链（MessageList
 * 一族）在没有 Provider 时也要能渲染（如欢迎页复用组件），所以额外提供
 * useArtifactsOptional —— 拿不到就返回 null，调用方据此降级为「不显示预览按钮」。
 */
export function useArtifacts(): ArtifactsContextValue {
  const ctx = useContext(ArtifactsContext)
  if (!ctx) {
    throw new Error('useArtifacts 必须在 ArtifactsProvider 内部使用')
  }
  return ctx
}

/** 宽松版：不在 Provider 内时返回 null（供共享组件降级用）。 */
export function useArtifactsOptional(): ArtifactsContextValue | null {
  return useContext(ArtifactsContext)
}
