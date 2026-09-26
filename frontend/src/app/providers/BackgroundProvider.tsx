import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

/**
 * 背景主题（BackgroundProvider）
 *
 * 职责：全站唯一的"外观"入口——在 <html> 上设置 data-bg，驱动 index.css 里的
 *      三套背景令牌（paper 静谧纸感 / glass 深空玻璃 / mist 柔和雾彩），并持久化到
 *      localStorage。已移除旧的 light/dark 明暗开关（背景本身覆盖明暗）。
 * 默认：paper（静谧纸感）。
 */

export type BackgroundKey = 'paper' | 'glass' | 'mist'

interface BackgroundMeta {
  key: BackgroundKey
  name: string
  desc: string
}

// 三套背景的元信息（供背景选择器展示）
export const BACKGROUNDS: BackgroundMeta[] = [
  { key: 'paper', name: '静谧纸感 · 暖中性', desc: '米白纸底 + 柔光，克制优雅' },
  { key: 'glass', name: '深空玻璃 · 冷调', desc: '近黑蓝 + 青蓝微光，科技感' },
  { key: 'mist', name: '柔和雾彩 · 轻盈', desc: '低饱和渐变雾 + 大留白' },
]

const STORAGE_KEY = 'prism-bg'
const DEFAULT_BG: BackgroundKey = 'paper'

function isBackground(v: string | null): v is BackgroundKey {
  return v === 'paper' || v === 'glass' || v === 'mist'
}

function readStored(): BackgroundKey {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (isBackground(saved)) return saved
  } catch {
    // localStorage 不可用时回落默认
  }
  return DEFAULT_BG
}

interface BackgroundContextValue {
  background: BackgroundKey
  setBackground: (key: BackgroundKey) => void
}

const BackgroundContext = createContext<BackgroundContextValue | null>(null)

export function BackgroundProvider({ children }: { children: ReactNode }) {
  const [background, setBackgroundState] = useState<BackgroundKey>(readStored)

  // 1.背景变化时写回 <html data-bg> 并持久化
  useEffect(() => {
    document.documentElement.setAttribute('data-bg', background)
    try {
      localStorage.setItem(STORAGE_KEY, background)
    } catch {
      // 忽略持久化失败（隐私模式等）
    }
  }, [background])

  const setBackground = useCallback((key: BackgroundKey) => setBackgroundState(key), [])

  const value = useMemo(() => ({ background, setBackground }), [background, setBackground])
  return <BackgroundContext.Provider value={value}>{children}</BackgroundContext.Provider>
}

export function useBackground(): BackgroundContextValue {
  const ctx = useContext(BackgroundContext)
  if (!ctx) throw new Error('useBackground 必须在 <BackgroundProvider> 内使用')
  return ctx
}
