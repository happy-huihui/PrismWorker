import { useEffect, useMemo, useRef, useState } from 'react'

import { fetchArtifact, parseArtifactError } from './utils'
import { htmlToBlobUrl, previewModeOf, type PreviewMode } from './preview'

/** 原始文本加载结果（供 HTML 源码视图使用）。 */
export type ArtifactText =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; text: string; size: number }

/**
 * 产物内容加载（useArtifactContent）
 *
 * 职责：给定 (threadId, path)，把产物拉回来并转成「可以直接喂给渲染层」的形态。
 *
 * 为什么统一走 fetch + blob 而不是直接把 URL 塞给 <img src>：
 *      产物下载路由要求鉴权（token 在 header 里），而 <img>/<iframe> 的 src
 *      无法带自定义 header，直接引用必然 401。所以图片/HTML 都先 fetch 成
 *      blob，再用 object URL 渲染。
 *
 * 生命周期：object URL 必须在替换或卸载时 revoke，否则每切一次文件就泄一个
 *      内存中的 blob（大图/长 HTML 下很明显）。
 */

export type ArtifactContent =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'image'; url: string; size: number }
  | { status: 'html'; url: string; size: number }
  | { status: 'markdown'; text: string }
  | { status: 'code'; text: string; lang: string }
  | { status: 'text'; text: string }
  | { status: 'binary'; size: number }

/** 从路径末段推断代码语言（给 MarkdownContent 的代码块标注）。 */
function langFromPath(path: string): string {
  const m = /\.([A-Za-z0-9_+-]+)$/.exec(path)
  return m ? m[1].toLowerCase() : 'text'
}

interface UseArtifactContentOptions {
  threadId: string
  /** 产物虚拟路径；null 表示不加载 */
  path: string | null
  /** 是否启用（侧边栏关闭时传 false，避免白拉流量） */
  enabled: boolean
}

export function useArtifactContent({ threadId, path, enabled }: UseArtifactContentOptions): ArtifactContent {
  const [content, setContent] = useState<ArtifactContent>({ status: 'idle' })
  const objectUrlRef = useRef<string | null>(null)

  // 形态在 path 变化时同步算出（避免 effect 里再判断一次、也避免闪烁）
  const mode = useMemo<PreviewMode | null>(() => (path ? previewModeOf(path) : null), [path])

  useEffect(() => {
    const revoke = () => {
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current)
        objectUrlRef.current = null
      }
    }

    if (!enabled || !path || !mode) {
      revoke()
      setContent({ status: 'idle' })
      return
    }

    const ctl = new AbortController()
    setContent({ status: 'loading' })

    /** 走 blob 路径渲染（图片 / HTML）：先拿 blob 再建 object URL。 */
    const loadBlob = async (): Promise<void> => {
      const resp = await fetchArtifact(threadId, path, ctl.signal)
      if (!resp.ok) {
        setContent({ status: 'error', message: parseArtifactError(resp) })
        return
      }
      if (mode === 'html') {
        // HTML 需要先转成文本才能注入 <base>，再包成 blob
        const text = await resp.text()
        revoke()
        const url = htmlToBlobUrl(text)
        objectUrlRef.current = url
        setContent({ status: 'html', url, size: new Blob([text]).size })
        return
      }
      // 图片：必须走 blob（text() 会把二进制字节当 UTF-8 解码，图会坏掉）
      const blob = await resp.blob()
      revoke()
      const url = URL.createObjectURL(blob)
      objectUrlRef.current = url
      setContent({ status: 'image', url, size: blob.size })
    }

    /** 走纯文本路径渲染（markdown / code / text）。 */
    const loadText = async (): Promise<void> => {
      const resp = await fetchArtifact(threadId, path, ctl.signal)
      if (!resp.ok) {
        setContent({ status: 'error', message: parseArtifactError(resp) })
        return
      }
      const text = await resp.text()
      if (mode === 'markdown') setContent({ status: 'markdown', text })
      else if (mode === 'code') setContent({ status: 'code', text, lang: langFromPath(path) })
      else setContent({ status: 'text', text })
    }

    /** 二进制：只报大小，让用户下载。 */
    const loadBinary = async (): Promise<void> => {
      const resp = await fetchArtifact(threadId, path, ctl.signal)
      if (!resp.ok) {
        setContent({ status: 'error', message: parseArtifactError(resp) })
        return
      }
      const blob = await resp.blob()
      setContent({ status: 'binary', size: blob.size })
    }

    const run =
      mode === 'image' || mode === 'html'
        ? loadBlob
        : mode === 'binary'
          ? loadBinary
          : loadText

    void (async () => {
      try {
        await run()
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setContent({
          status: 'error',
          message: err instanceof Error && err.message ? err.message : '产物加载失败',
        })
      }
    })()

    return () => {
      ctl.abort()
      revoke()
    }
  }, [threadId, path, mode, enabled])

  return content
}

/**
 * 原始文本加载（useArtifactText）
 *
 * 职责：拿产物的**未加工**原文，仅供 HTML「源码视图」使用。
 *
 * 为什么不复用 useArtifactContent：
 *      那个 hook 对 html 走的是 blob 分支，而 blob 里的内容已被注入过
 *      <base href="./" />。源码视图的语义是「所见即文件内容」，
 *      注入后的版本会误导用户（凭空多一行 <base>）。
 *      这里只 fetch + text()，不做任何改写。
 *
 * 开销：仅在用户主动切到源码态时启用（enabled=false 时不发请求）。
 */
export function useArtifactText({
  threadId,
  path,
  enabled,
}: UseArtifactContentOptions): ArtifactText {
  const [state, setState] = useState<ArtifactText>({ status: 'idle' })

  useEffect(() => {
    if (!enabled || !path) {
      setState({ status: 'idle' })
      return
    }

    const ctl = new AbortController()
    setState({ status: 'loading' })

    void (async () => {
      try {
        const resp = await fetchArtifact(threadId, path, ctl.signal)
        if (!resp.ok) {
          setState({ status: 'error', message: parseArtifactError(resp) })
          return
        }
        const text = await resp.text()
        setState({ status: 'ready', text, size: new Blob([text]).size })
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setState({
          status: 'error',
          message: err instanceof Error && err.message ? err.message : '源码加载失败',
        })
      }
    })()

    return () => ctl.abort()
  }, [threadId, path, enabled])

  return state
}
