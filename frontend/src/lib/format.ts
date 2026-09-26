import { format, formatDistanceToNow } from 'date-fns'
import { zhCN } from 'date-fns/locale'

/**
 * 时长人性化展示（run 级耗时 / 工具耗时共用）。
 *
 * 规则（为什么要分段）：纯秒数在长 run 下完全丧失可读性——
 * 「耗时 1245s」用户得自己心算才是 20 分钟；分段后一眼可读。
 *   - < 10 秒     → 保留一位小数（「3.2 秒」，能看出计时在动）
 *   - 10 ~ 60 秒  → 整秒（「45 秒」）
 *   - 1 ~ 60 分钟 → 「20 分 45 秒」
 *   - 超过 1 小时 → 「1 小时 24 分 5 秒」
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return ''
  const total = Math.floor(seconds)
  if (total < 60) {
    return total < 10 ? `${seconds.toFixed(1)} 秒` : `${total} 秒`
  }
  const sec = total % 60
  const min = Math.floor((total % 3600) / 60)
  const hour = Math.floor(total / 3600)
  if (hour === 0) return `${min} 分 ${sec} 秒`
  return `${hour} 小时 ${min} 分 ${sec} 秒`
}

export function formatRelativeTime(timestampSeconds: number): string {
  const date = new Date(timestampSeconds * 1000)
  const isStale = Date.now() - date.getTime() > 7 * 24 * 3600 * 1000
  if (isStale) return formatAbsoluteTime(timestampSeconds)
  return formatDistanceToNow(date, { addSuffix: true, locale: zhCN })
}

export function formatAbsoluteTime(timestampSeconds: number): string {
  return format(new Date(timestampSeconds * 1000), 'yyyy-MM-dd HH:mm', { locale: zhCN })
}

/**
 * 会话列表用的紧凑相对时间：刚刚 / N分钟 / N小时 / N天（超过一周回退绝对日期）。
 * 与 formatRelativeTime 的区别：不带「…前」后缀，更贴合侧栏窄位的展示密度。
 */
export function formatThreadTime(timestampSeconds: number): string {
  const then = timestampSeconds * 1000
  const diffMs = Date.now() - then
  const minutes = Math.floor(diffMs / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes}分钟`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}小时`
  const days = Math.floor(hours / 24)
  if (days <= 7) return `${days}天`
  return formatAbsoluteTime(timestampSeconds)
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