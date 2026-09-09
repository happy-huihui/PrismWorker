//        <空行>

export interface SSEFrame {
  event: string
  data: string
}

export function parseSSEFrame(raw: string): SSEFrame | null {
  if (!raw || !raw.trim()) return null

  let event = 'message'
  const dataLines: string[] = []
  for (const line of raw.split('\n')) {
    if (line === '' || line.startsWith(':')) continue
    const idx = line.indexOf(':')
    const field = idx === -1 ? line : line.slice(0, idx)
    let value = idx === -1 ? '' : line.slice(idx + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    else if (field === 'data') dataLines.push(value)
  }

  if (dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}

export function parseSSEData<T = unknown>(frame: SSEFrame): T | null {
  try {
    return JSON.parse(frame.data) as T
  } catch {
    return null
  }
}

export async function readSSEStream(
  response: Response,
  onEvent: (frame: SSEFrame) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (!response.body) throw new Error('SSE 响应无 body')

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')

      let sep = buffer.indexOf('\n\n')
      while (sep !== -1) {
        const rawFrame = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        const frame = parseSSEFrame(rawFrame)
        if (frame) onEvent(frame)
        sep = buffer.indexOf('\n\n')
      }
    }

    const tail = buffer.trim()
    if (tail) {
      const frame = parseSSEFrame(tail)
      if (frame) onEvent(frame)
    }
  } finally {
    reader.releaseLock()
    void signal
  }
}