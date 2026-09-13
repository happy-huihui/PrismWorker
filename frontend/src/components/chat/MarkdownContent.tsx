import { useDeferredValue, useEffect, useState, type ReactNode } from 'react'
import { Check, Copy } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { toast } from 'sonner'
import { codeToHtml } from 'shiki'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'

import { cn } from '@/lib/utils'

import 'katex/dist/katex.min.css'

interface MarkdownContentProps {
  text: string
  /** 流式输出中：跳过 shiki 高亮（性能），仅渲染纯文本代码块 */
  streaming?: boolean
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

  useEffect(() => {
    if (streaming) {
      setHtml(null)
      return
    }
    let cancelled = false
    codeToHtml(code, { lang: normalizeLang(lang), theme: 'github-dark' })
      .then((h) => {
        if (!cancelled) setHtml(h)
      })
      .catch(() => {
        if (!cancelled) setHtml(null)
      })
    return () => {
      cancelled = true
    }
  }, [code, lang, streaming])

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
    <div className="group/code relative my-2 overflow-hidden rounded-lg border bg-zinc-950 text-sm">
      <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-900/80 px-3 py-1.5">
        <span className="text-xs text-zinc-400">{lang || 'text'}</span>
        <button
          type="button"
          onClick={copy}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100"
          title="复制代码"
        >
          {copied ? <Check className="size-3.5 text-emerald-400" /> : <Copy className="size-3.5" />}
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

export function MarkdownContent({ text, streaming }: MarkdownContentProps) {
  const deferred = useDeferredValue(text)

  return (
    <div
      className={cn(
        'markdown-content w-full break-words text-sm leading-relaxed',
        '[&>*:first-child]:mt-0 [&>*:last-child]:mb-0',
        '[&_p]:my-1.5',
        '[&_h1]:mb-1.5 [&_h1]:mt-3 [&_h1]:text-xl [&_h1]:font-semibold',
        '[&_h2]:mb-1.5 [&_h2]:mt-3 [&_h2]:text-lg [&_h2]:font-semibold',
        '[&_h3]:mb-1 [&_h3]:mt-2.5 [&_h3]:text-base [&_h3]:font-semibold',
        '[&_h4]:mb-0.5 [&_h4]:mt-2.5 [&_h4]:text-sm [&_h4]:font-semibold',
        '[&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5',
        '[&_ol]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5',
        '[&_li]:my-0.5',
        '[&_blockquote]:my-1.5 [&_blockquote]:border-l-2 [&_blockquote]:border-muted [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground',
        '[&_table]:my-1.5 [&_table]:w-full [&_table]:border-collapse [&_table]:text-sm',
        '[&_th]:border [&_th]:border-border [&_th]:bg-muted [&_th]:px-2 [&_th]:py-1 [&_th]:text-left',
        '[&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1',
        '[&_code:not(pre code)]:rounded [&_code:not(pre code)]:bg-muted [&_code:not(pre code)]:px-1 [&_code:not(pre code)]:py-0.5 [&_code:not(pre code)]:font-mono [&_code:not(pre code)]:text-[0.85em]',
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