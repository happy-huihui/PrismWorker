import { artifactUrl, artifactKind, type ArtifactKind } from './utils'

/**
 * 产物预览形态判定与 HTML 预览处理（preview）
 *
 * 职责：把「一个产物路径」翻译成「该怎么预览它」，并处理 HTML 预览的两个
 *      特有麻烦：鉴权（iframe src 带不了 header）与相对路径资源解析。
 *
 * 参考 DeerFlow 的做法（artifact-file-detail.tsx + core/artifacts/preview.ts）：
 *    - HTML 在 iframe 里预览，sandbox 故意**不给 allow-same-origin**，
 *      让文档处于不透明源（opaque origin）：脚本能跑，但拿不到父页面的
 *      document / cookie / localStorage，把「模型生成的 HTML 可能带脚本」
 *      的风险圈在 iframe 内。
 *    - 因为是不透明源，iframe 里拿不到父页面的东西，也不需要注入回传脚本，
 *      所以我方实现比 DeerFlow 更简单：只注入 <base> 让相对路径能解析。
 */

/** 预览形态：决定 ArtifactFileDetail 用哪个分支渲染。 */
export type PreviewMode = 'image' | 'html' | 'markdown' | 'code' | 'text' | 'binary'

/** 可内联预览的 HTML 类扩展名。 */
const HTML_EXT = /\.(html?|htm)$/i

/**
 * 判断产物是否应该走 iframe 渲染。
 *
 * 注意与 artifactKind 的分工：artifactKind 把 html 归为 "code"（因为它确实
 * 是代码），但从**预览体验**看 html 该渲染成网页而不是看源码。这里做的是
 * 「预览形态」判断，优先级高于 artifactKind。
 */
export function isHtmlArtifact(path: string): boolean {
  return HTML_EXT.test(path)
}

/** 产物路径 → 预览形态。 */
export function previewModeOf(path: string): PreviewMode {
  if (isHtmlArtifact(path)) return 'html'
  const kind: ArtifactKind = artifactKind(path)
  if (kind === 'image') return 'image'
  if (kind === 'markdown') return 'markdown'
  if (kind === 'code') return 'code'
  if (kind === 'text') return 'text'
  return 'binary'
}

/** <base> 注入锚点（DeerFlow 用同样思路，让相对路径资源能定位）。 */
const BASE_HREF = '<base href="./" />'
const HEAD_OPEN_RE = /<head(\s[^>]*)?>/i
const HTML_OPEN_RE = /<html(\s[^>]*)?>/i

/**
 * 给 HTML 文本注入 <base href>，让其中的相对路径资源能正常解析。
 *
 * 为什么需要：我们把 HTML 内容做成 blob URL 塞进 iframe，blob URL 的
 * 「目录」是无效的，HTML 里写的 `<img src="chart.png">` 之类相对路径
 * 会解析失败。注入 <base> 后至少指向同源根；真正要完整渲染带外部资源的
 * 页面，建议模型输出自包含单文件（提示词里已说明）。
 *
 * 注入位置优先级：<head ...> 内 → <html ...> 之后 → 直接前置。
 * 已有 <base> 则不重复注入。
 */
export function injectHtmlBase(html: string): string {
  if (/<base\s/i.test(html)) return html

  const headMatch = HEAD_OPEN_RE.exec(html)
  if (headMatch) {
    const end = headMatch.index + headMatch[0].length
    return `${html.slice(0, end)}${BASE_HREF}${html.slice(end)}`
  }

  const htmlMatch = HTML_OPEN_RE.exec(html)
  if (htmlMatch) {
    const end = htmlMatch.index + htmlMatch[0].length
    // <html> 后没有 <head> 时，补一个 head 块承载 base
    return `${html.slice(0, end)}<head>${BASE_HREF}</head>${html.slice(end)}`
  }

  // 连 <html> 都没有的片段：直接前置，浏览器会自行补全
  return `${BASE_HREF}${html}`
}

/**
 * 把 HTML 文本包成可直接给 iframe 用的 blob URL。
 *
 * 调用方拿到的 URL 必须自己负责 revoke（见 ArtifactFileDetail 的 effect 清理）。
 */
export function htmlToBlobUrl(html: string): string {
  const blob = new Blob([injectHtmlBase(html)], { type: 'text/html;charset=utf-8' })
  return URL.createObjectURL(blob)
}

/**
 * iframe 的 sandbox 策略。
 *
 * 与 DeerFlow 保持一致：允许脚本与表单，**不给** allow-same-origin。
 * 这意味着 iframe 内的文档处于不透明源，无法访问父页面 DOM/Cookie，
 * 也无法发起同源带凭据的请求 —— 对「模型生成的 HTML」是必要的隔离。
 */
export const HTML_IFRAME_SANDBOX = 'allow-scripts allow-forms'

/** 产物下载 URL（用同一份解码逻辑，避免两处 hand-rolled encode）。 */
export function previewUrl(threadId: string, path: string): string {
  return artifactUrl(threadId, path)
}
