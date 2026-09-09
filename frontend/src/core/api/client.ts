import { type ApiErrorShape } from './types'

export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) || '/api'
export const USER_ID: string = 'default'

export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

function parseDetail(text: string): string {
  try {
    const body = JSON.parse(text) as ApiErrorShape
    if (typeof body?.detail === 'string') return body.detail
    if (Array.isArray(body?.detail)) {
      const msgs = body.detail.map((d) => d?.msg ?? '').filter(Boolean)
      if (msgs.length) return msgs.join('；')
    }
  } catch {
  }
  return ''
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const isForm = init.body instanceof FormData
  if (init.body != null && !isForm && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  headers.set('X-User-Id', USER_ID)

  const body = init.body != null && !isForm && typeof init.body === 'object' && !(init.body instanceof Blob)
    ? JSON.stringify(init.body)
    : init.body

  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers, body })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw err
    }
    throw new ApiError(0, `网络请求失败：${err instanceof Error ? err.message : String(err)}`)
  }

  if (res.status === 204) return undefined as T

  const text = await res.text()

  if (!res.ok) {
    const detail = parseDetail(text) || `请求失败（HTTP ${res.status}）`
    throw new ApiError(res.status, detail)
  }

  if (!text) return undefined as T

  return JSON.parse(text) as T
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { method: 'GET', signal }),
  post: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, { method: 'POST', body: body as BodyInit, signal }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body: body as BodyInit }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}