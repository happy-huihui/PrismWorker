import { format, formatDistanceToNow } from 'date-fns'
import { zhCN } from 'date-fns/locale'

export function formatRelativeTime(timestampSeconds: number): string {
  const date = new Date(timestampSeconds * 1000)
  const isStale = Date.now() - date.getTime() > 7 * 24 * 3600 * 1000
  if (isStale) return formatAbsoluteTime(timestampSeconds)
  return formatDistanceToNow(date, { addSuffix: true, locale: zhCN })
}

export function formatAbsoluteTime(timestampSeconds: number): string {
  return format(new Date(timestampSeconds * 1000), 'yyyy-MM-dd HH:mm', { locale: zhCN })
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return unit === 0 ? `${Math.round(value)} ${units[unit]}` : `${value.toFixed(1)} ${units[unit]}`
}