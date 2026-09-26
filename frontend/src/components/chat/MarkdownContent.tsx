import { useDeferredValue, useEffect, useState, type ReactNode } from 'react'
import { Check, Copy } from '@/components/icons'
import ReactMarkdown from 'react-markdown'
import { toast } from 'sonner'
import { codeToHtml } from 'shiki'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'

import { useBackground } from '@/app/providers/BackgroundProvider'
import { cn } from '@/lib/utils'

import 'katex/dist/katex.min.css'

interface MarkdownContentProps {
  text: string
  /** 流式输出中：跳过 shiki 高亮（性能），仅渲染纯文本代码块 */
  streaming?: boolean
  /** 追加到根容器的类名（思考链里需要压小字号、换前景色） */
  className?: string
}

function inlineText(node: ReactNode): string {
  if (node == null) return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(inlineText).join('')
  if (typeof node === 'object' && 'props' in node) {
    return inlineText((node as { props: { children?: ReactNode } }).props.children)
  }
  return ''
}

const LANG_ALIASES: Record<string, string> = {
  js: 'javascript',
  ts: 'typescript',
  tsx: 'tsx',
  jsx: 'jsx',
  py: 'python',
  sh: 'bash',
  shell: 'bash',
  zsh: 'bash',
  yml: 'yaml',
  md: 'markdown',
  mdx: 'markdown',
  json: 'json',
  txt: 'text',
  text: 'text',
  html: 'html',
  css: 'css',
  sql: 'sql',
  http: 'http',
  diff: 'diff',
}
function normalizeLang(lang: string): string {
  const l = lang.trim().toLowerCase()
  return LANG_ALIASES[l] ?? (l || 'text')
}

function CodeBlock({ code, lang, streaming }: { code: string; lang: string; streaming?: boolean }) {
  const [html, setHtml] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  // 代码块主题跟随背景：深空玻璃用 github-dark，纸感/雾彩用 github-light（模板 .code 亮色卡纸底）
  const { background } = useBackground()
  const dark = background === 'glass'

  useEffect(() => {
    if (streaming) {
      setHtml(null)
      return
    }
    let cancelled = false
    codeToHtml(code, { lang: normalizeLang(lang), theme: dark ? 'github-dark' : 'github-light' })
      .then((h) => {
        if (!cancelled) setHtml(h)
      })
      .catch(() => {
        if (!cancelled) setHtml(null)
      })
    return () => {
      cancelled = true
    }
  }, [code, lang, streaming, dark])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error('复制失败', { description: '剪贴板不可用，请手动选择复制' })
    }
  }

  return (
    <div
      className={cn(
        'group/code relative my-2 overflow-hidden rounded-[10px] border text-sm',
        dark ? 'bg-zinc-950 text-zinc-100' : 'bg-card text-foreground',
      )}
    >
      <div
        className={cn(
          'flex items-center justify-between border-b px-3 py-1.5 text-xs',
          dark ? 'border-zinc-800 bg-zinc-900/80 text-zinc-400' : 'border-border/70 bg-muted/40 text-muted-foreground',
        )}
      >
        <span>{lang || 'text'}</span>
        <button
          type="button"
          onClick={copy}
          className={cn(
            'flex items-center gap-1 rounded px-1.5 py-0.5 text-xs transition-colors',
            dark ? 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
          )}
          title="复制代码"
        >
          {copied ? <Check className="size-3.5 text-emerald-500" /> : <Copy className="size-3.5" />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      {html ? (
        <div
          className="overflow-x-auto p-3 text-[13px] leading-relaxed [&_pre]:bg-transparent [&_pre]:p-0"
          // eslint-disable-next-line react/no-danger —— shiki 输出为受控样式化 HTML
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <pre className="overflow-x-auto p-3 text-[13px] leading-relaxed">
          <code>{code}</code>
        </pre>
      )}
    </div>
  )
}

export function MarkdownContent({ text, streaming, className }: MarkdownContentProps) {
  const deferred = useDeferredValue(text)

  return (
    <div
      className={cn(
        'markdown-content w-full break-words text-[15px] leading-[1.7]',
        className,
        '[&>*:first-child]:mt-0 [&>*:last-child]:mb-0',
        '[&_p]:my-1.5',
        '[&_h1]:mb-1.5 [&_h1]:mt-3 [&_h1]:font-display [&_h1]:text-xl [&_h1]:font-semibold',
        '[&_h2]:mb-1.5 [&_h2]:mt-3 [&_h2]:font-display [&_h2]:text-lg [&_h2]:font-semibold',
        '[&_h3]:mb-1 [&_h3]:mt-2.5 [&_h3]:font-display [&_h3]:text-base [&_h3]:font-semibold',
        '[&_h4]:mb-0.5 [&_h4]:mt-2.5 [&_h4]:font-display [&_h4]:text-sm [&_h4]:font-semibold',
        '[&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5',
        '[&_ol]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5',
        '[&_li]:my-0.5',
        '[&_blockquote]:my-1.5 [&_blockquote]:border-l-2 [&_blockquote]:border-muted [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground',
        '[&_table]:my-1.5 [&_table]:w-full [&_table]:border-collapse [&_table]:text-sm',
        '[&_th]:border [&_th]:border-border [&_th]:bg-muted [&_th]:px-2 [&_th]:py-1 [&_th]:text-left',
        '[&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1',
        '[&_code:not(pre code)]:rounded-md [&_code:not(pre code)]:bg-secondary [&_code:not(pre code)]:px-1.5 [&_code:not(pre code)]:py-0.5 [&_code:not(pre code)]:font-mono [&_code:not(pre code)]:text-[0.85em]',
        '[&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2',
        '[&_hr]:my-2.5 [&_hr]:border-border',
        '[&_img]:my-1.5 [&_img]:max-w-full [&_img]:rounded-md',
        '[&_pre]:my-0',
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeKatex]}
        components={{
          pre: (props) => {
            const { children } = props
            const child = Array.isArray(children) ? children[0] : children
            if (child && typeof child === 'object' && 'props' in child) {
              const childProps = child.props as {
                className?: string
                children?: ReactNode
              }
              const match = /language-([\w+-]+)/.exec(childProps.className ?? '')
              const codeText = inlineText(childProps.children)
              return (
                <CodeBlock
                  code={codeText.replace(/\n$/, '')}
                  lang={match?.[1] ?? 'text'}
                  streaming={streaming}
                />
              )
            }
            return <pre className="rounded-lg bg-muted p-3">{children}</pre>
          },
          code: (props) => (
            <code className={cn('font-mono', props.className)}>{props.children}</code>
          ),
        }}
      >
        {deferred}
      </ReactMarkdown>
    </div>
  )
}