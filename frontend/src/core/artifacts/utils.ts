import { API_BASE, USER_ID } from '@/core/api/client'

const OUTPUTS_PREFIX = '/mnt/user-data/outputs/'

export function virtualPathToRel(virtualPath: string): string {
  const v = (virtualPath || '').trim()
  if (!v.startsWith(OUTPUTS_PREFIX)) return ''
  return v.slice(OUTPUTS_PREFIX.length).replace(/^\/+|\/+$/g, '')
}

export function virtualPathToFilename(virtualPath: string): string {
  const rel = virtualPathToRel(virtualPath)
  if (!rel) return virtualPath || ''
  return rel.split('/').pop() ?? rel
}

export function artifactUrl(threadId: string, virtualPath: string, download = false): string {
  const rel = virtualPathToRel(virtualPath)
  if (!rel) return ''
  const encoded = rel
    .split('/')
    .map((seg) => encodeURIComponent(seg))
    .join('/')
  return `${API_BASE}/threads/${threadId}/artifacts/${encoded}${download ? '?download=1' : ''}`
}

export function isImagePath(virtualPath: string): boolean {
  return /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i.test(virtualPath)
}

export function isMarkdownPath(virtualPath: string): boolean {
  return /\.(md|markdown|txt)$/i.test(virtualPath)
}


export type ArtifactKind = 'image' | 'markdown' | 'code' | 'text' | 'other'

const CODE_EXT = /\.(py|js|jsx|ts|tsx|json|css|scss|html|htm|xml|yaml|yml|sql|sh|bash|zsh|go|rs|c|cpp|h|hpp|java|kt|swift|php|rb|lua|r|toml|ini|env|gitignore|dockerfile)$/i

const TEXT_EXT = /\.(log|csv|tsv|conf|cfg|properties|txt)$/i

export function artifactKind(virtualPath: string): ArtifactKind {
  if (isImagePath(virtualPath)) return 'image'
  if (/\.(md|markdown)$/i.test(virtualPath)) return 'markdown'
  if (CODE_EXT.test(virtualPath)) return 'code'
  if (TEXT_EXT.test(virtualPath)) return 'text'
  return 'other'
}


export async function fetchArtifact(
  threadId: string,
  virtualPath: string,
  signal?: AbortSignal,
): Promise<Response> {
  const url = artifactUrl(threadId, virtualPath)
  if (!url) throw new Error('非法产物路径')
  return fetch(url, { headers: { 'X-User-Id': USER_ID }, signal })
}

export function parseArtifactError(resp: Response): string {
  const status = resp.status
  if (status === 400) return '产物路径非法（越界）'
  if (status === 404) return '产物不存在'
  return `产物获取失败（HTTP ${status}）`
}

export async function downloadArtifact(threadId: string, virtualPath: string): Promise<void> {
  const resp = await fetchArtifact(threadId, virtualPath)
  if (!resp.ok) throw new Error(parseArtifactError(resp))
  const blob = await resp.blob()
  const href = URL.createObjectURL(blob)
  const name = virtualPathToFilename(virtualPath)
  try {
    const a = document.createElement('a')
    a.href = href
    a.download = name
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(href), 2000)
  }
}