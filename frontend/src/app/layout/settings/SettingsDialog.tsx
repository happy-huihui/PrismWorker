import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import { useAuth } from '@/app/providers/AuthProvider'
import { BACKGROUNDS, useBackground, type BackgroundKey } from '@/app/providers/BackgroundProvider'
import { FileText, Palette, Paperclip, Plus, Settings, Skill, Users } from '@/components/icons'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { useAgentMd, useSaveAgentMd } from '@/core/agent-md'
import { useInstallSkill, useSaveSkillBlacklist, useSkills, type SkillFinding } from '@/core/skills'
import { cn } from '@/lib/utils'

/**
 * 设置弹窗（SettingsDialog）
 *
 * 职责：WorkBuddy 式「左菜单 + 右内容」设置面板。当前可用：账户设置（身份展示 +
 *      退出登录）、外观（三套背景主题卡）、技能（用户级启停黑名单）、
 *      自定义指令（agent.md）；通用为禁用占位。
 * 风格：对齐 LoginDialog 的卡片视觉（rounded-[18px] bg-card 柔和投影），
 *      菜单激活态用 bg-primary/10 text-primary。
 */

// 设置面板路由：菜单项 → 右侧内容
type SettingsPanel = 'account' | 'appearance' | 'skills' | 'instruction'

// 主题卡预览色板（与 index.css 三套主色一致，复用自旧 BackgroundPicker）
const SWATCH: Record<BackgroundKey, string> = {
  paper: 'linear-gradient(135deg,#f3efe7,#efe4d2)',
  glass: 'linear-gradient(135deg,#0a0d14,#123 60%,#0a0d14)',
  mist: 'linear-gradient(135deg,#ffd9c2,#cfe9ff 50%,#cdf0d8)',
}

// 主题卡迷你窗口 mock 的配色（亮色主题用深色线条，深色主题用亮色线条）
const MOCK: Record<BackgroundKey, { card: string; line: string }> = {
  paper: { card: 'rgba(255,255,255,.8)', line: 'rgba(60,45,20,.22)' },
  glass: { card: 'rgba(255,255,255,.07)', line: 'rgba(255,255,255,.28)' },
  mist: { card: 'rgba(255,255,255,.7)', line: 'rgba(42,40,51,.18)' },
}

// 左菜单项元信息（账户固定第一；通用为禁用占位）
const MENU: Array<{
  key: SettingsPanel
  label: string
  icon: typeof Settings
  disabled?: boolean
}> = [
  { key: 'account', label: '账户设置', icon: Users },
  { key: 'appearance', label: '外观', icon: Palette },
  { key: 'skills', label: '技能', icon: Skill },
  { key: 'instruction', label: '自定义指令', icon: FileText },
]

export function SettingsDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { userId, openLogin, logout } = useAuth()
  // 仅弹窗打开且已登录时拉取（避免无意义的 401）
  const { data } = useAgentMd({ enabled: open && !!userId })
  const saveAgentMd = useSaveAgentMd()

  // 面板路由 + 自定义指令编辑态（跨开合保留，避免误关丢失上下文）
  const [panel, setPanel] = useState<SettingsPanel>('account')
  const [content, setContent] = useState('')
  const [savedContent, setSavedContent] = useState('')

  const maxLength = data?.max_length ?? 5_000
  // 与已保存内容（strip 后）不一致即视为有改动
  const dirty = content.trim() !== savedContent.trim()
  const overLimit = content.length > maxLength

  // 打开时（或保存后数据回填时）同步自定义指令编辑态
  useEffect(() => {
    if (open) {
      setContent(data?.content ?? '')
      setSavedContent(data?.content ?? '')
    }
  }, [open, data?.content])

  const handleSave = () => {
    saveAgentMd.mutate(
      { content },
      {
        onSuccess: (next) => {
          setSavedContent(next.content)
          setContent(next.content)
          toast.success(next.content.trim() ? '自定义指令已保存，对新会话生效' : '自定义指令已清空')
        },
        onError: (err) =>
          toast.error('保存失败', {
            description: err instanceof Error ? err.message : '未知错误',
          }),
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton
        className="max-w-[860px] sm:max-w-[860px] gap-0 overflow-hidden rounded-[18px] border-input bg-card p-0 shadow-[0_2px_4px_rgba(60,45,20,.06),0_24px_60px_rgba(60,45,20,.18)]"
      >
        {/* grid-rows-[minmax(0,1fr)]：把行轨道钉死在容器高度内，子列才可能出滚动条 */}
        <div className="grid h-[min(620px,88vh)] grid-cols-[190px_1fr] grid-rows-[minmax(0,1fr)]">
          {/* 1.左菜单栏 */}
          <div className="flex flex-col gap-4 border-r bg-sidebar/70 px-3 py-5">
            <div className="px-2.5 text-[15px] font-semibold">设置</div>
            <nav className="flex flex-col gap-0.5">
              {MENU.map(({ key, label, icon: Icon, disabled }) => {
                const active = panel === key
                return (
                  <button
                    key={key}
                    type="button"
                    disabled={disabled}
                    onClick={() => !disabled && setPanel(key)}
                    aria-current={active || undefined}
                    className={cn(
                      'flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] transition-colors',
                      active
                        ? 'bg-primary/10 font-medium text-primary'
                        : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
                      disabled && 'opacity-50 hover:bg-transparent hover:text-muted-foreground',
                    )}
                  >
                    <Icon className="size-4" />
                    {label}
                    {disabled && <span className="ml-auto text-[10px] text-muted-foreground">开发中</span>}
                  </button>
                )
              })}
            </nav>
          </div>

          {/* 2.右内容区（按面板路由）；min-h-0 允许列收缩、把滚动交给内部区域 */}
          <div className="flex min-h-0 min-w-0 flex-col">
            {panel === 'account' && (
              <>
                <div className="border-b px-6 py-4">
                  <div className="text-[15px] font-semibold">账户设置</div>
                  <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
                    查看当前登录身份，或退出本设备登录。
                  </p>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
                  {userId ? (
                    <div className="flex items-center gap-4">
                      {/* 身份卡片：头像 + user_id */}
                      <span className="grid size-12 shrink-0 place-items-center rounded-full bg-foreground text-lg font-semibold text-background">
                        {userId.charAt(0).toUpperCase()}
                      </span>
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">{userId}</div>
                        <div className="text-xs text-muted-foreground">当前登录身份</div>
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        className="ml-auto text-destructive hover:text-destructive"
                        onClick={() => {
                          onOpenChange(false)
                          logout()
                        }}
                      >
                        退出登录
                      </Button>
                    </div>
                  ) : (
                    <div className="flex flex-col items-start gap-3">
                      <p className="text-sm text-muted-foreground">当前未登录，登录后可使用自定义指令等功能。</p>
                      <Button size="sm" onClick={() => { onOpenChange(false); openLogin() }}>
                        去登录
                      </Button>
                    </div>
                  )}
                </div>
              </>
            )}

            {panel === 'appearance' && <AppearancePanel />}

            {panel === 'skills' && <SkillsPanel />}

            {panel === 'instruction' && (
              <>
                <div className="border-b px-6 py-4">
                  <div className="text-[15px] font-semibold">自定义指令</div>
                  <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
                    保存后注入到每次对话的系统提示词末尾（agent.md），对主代理的所有任务生效。
                  </p>
                </div>
                <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-6 py-4">
                  <Textarea
                    value={content}
                    onChange={(e) => setContent(e.target.value)}
                    maxLength={maxLength}
                    placeholder={
                      '用 Markdown 写下你的个人偏好与协作约定，例如：\n\n' +
                      '- 始终用简体中文回复，语气简洁直接\n' +
                      '- 代码注释用中文，动词开头\n' +
                      '- 生成网页时优先使用暖色系设计'
                    }
                    className="min-h-0 flex-1 resize-none rounded-[12px] bg-background text-[13px] leading-6"
                    aria-label="自定义指令内容"
                  />
                  <p className="text-xs leading-5 text-muted-foreground">
                    适合写语言偏好、回复风格、工作流程约定等；与当前任务的明确要求冲突时以当前要求为准。
                    请勿填写密码、密钥等敏感信息。
                  </p>
                </div>
                <div className="flex items-center justify-between border-t px-6 py-3">
                  {/* 左：字数与未保存状态 */}
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className={cn(overLimit && 'text-destructive')}>
                      {content.length} / {maxLength}
                    </span>
                    {dirty && !overLimit && <span className="text-primary">未保存</span>}
                    {overLimit && <span>超出长度上限</span>}
                  </div>
                  {/* 右：清空 + 保存 */}
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setContent('')}
                      disabled={(!content && !dirty) || saveAgentMd.isPending}
                    >
                      清空
                    </Button>
                    <Button
                      size="sm"
                      onClick={handleSave}
                      disabled={!dirty || overLimit || saveAgentMd.isPending}
                    >
                      {saveAgentMd.isPending ? '保存中…' : '保存'}
                    </Button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 技能面板（SkillsPanel）
 *
 * DeerFlow 式结构：公共 / 自定义两个分栏（公共 = 内置开箱即用；自定义 = 用户
 * 自行安装，按用户目录隔离）+ 右上「安装技能」入口。每项一张卡（名称 + 描述 +
 * 启停开关），默认全部启用；关闭即加入当前用户黑名单（后端装配时跳过）。
 */
function SkillsPanel() {
  const { data, isLoading } = useSkills()
  const saveBlacklist = useSaveSkillBlacklist()
  const skills = data?.skills ?? []
  const [tab, setTab] = useState<'public' | 'custom'>('public')
  const [installOpen, setInstallOpen] = useState(false)

  const publicSkills = skills.filter((s) => s.source === 'public')
  const customSkills = skills.filter((s) => s.source === 'custom')
  const visible = tab === 'public' ? publicSkills : customSkills

  // 开关切换：基于当前清单计算下一版黑名单（全部开启 = 空黑名单）
  const handleToggle = (name: string, enabled: boolean) => {
    const next = skills.filter((s) => s.blocked && s.name !== name).map((s) => s.name)
    if (!enabled) next.push(name)
    saveBlacklist.mutate({ blocked: next })
  }

  return (
    <>
      <div className="border-b px-6 py-4">
        <div className="text-[15px] font-semibold">技能</div>
        <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
          管理 Agent 可用的技能：默认全部启用，关闭后该技能不再装配，也不会出现在 / 列表中。
        </p>
      </div>

      {/* 分栏 tabs + 安装入口（DeerFlow 式一行排布） */}
      <div className="flex items-center justify-between border-b px-6 py-2.5">
        <div className="flex items-center gap-1">
          {(
            [
              ['public', '公共', publicSkills.length],
              ['custom', '自定义', customSkills.length],
            ] as const
          ).map(([key, label, count]) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={cn(
                'border-b-2 px-2.5 py-1.5 text-[13px] transition-colors',
                tab === key
                  ? 'border-primary font-medium text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground',
              )}
            >
              {label}
              <span className="ml-1 text-[11px] text-muted-foreground">({count})</span>
            </button>
          ))}
        </div>
        <Button variant="outline" size="sm" onClick={() => setInstallOpen(true)}>
          <Plus className="size-3.5" />
          安装技能
        </Button>
      </div>

      {/* 技能卡片列表 */}
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-6 py-4">
        {isLoading && (
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-[88px] animate-pulse rounded-[14px] bg-secondary" />
            ))}
          </div>
        )}
        {!isLoading && visible.length === 0 && (
          <p className="text-sm text-muted-foreground">
            {tab === 'custom'
              ? '还没有自定义技能：点右上角「安装技能」，指定一个含 SKILL.md 的本地技能目录。'
              : '当前没有可用的公共技能。'}
          </p>
        )}
        {visible.map((skill) => (
          <div
            key={skill.name}
            className="flex items-start gap-4 rounded-[14px] border border-input bg-background/50 px-4 py-3.5"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-[15px] font-medium">/{skill.name}</span>
                {skill.source === 'custom' && (
                  <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] text-primary">
                    自定义
                  </span>
                )}
              </div>
              {skill.description && (
                <p className="mt-1 line-clamp-3 text-[13px] leading-5 text-muted-foreground">
                  {skill.description}
                </p>
              )}
            </div>
            {/* 启停开关：开 = 可用；关 = 拉黑 */}
            <Switch
              checked={!skill.blocked}
              onCheckedChange={(checked) => handleToggle(skill.name, checked)}
              disabled={saveBlacklist.isPending}
              aria-label={`启用技能 ${skill.name}`}
            />
          </div>
        ))}
      </div>

      {/* 安装技能弹窗 */}
      <InstallSkillDialog
        open={installOpen}
        onOpenChange={setInstallOpen}
        onInstalled={() => setTab('custom')}
      />
    </>
  )
}

/**
 * 安装技能弹窗（InstallSkillDialog）
 *
 * 选择 .skill 归档文件（ZIP）→ 上传后端 → 安全解压 + 纯静态审查
 * （blocker/error 清零才放行）→ 同名覆盖装入用户私有技能目录并默认启用。
 * 审查失败时弹窗内联展示发现清单，不关闭，方便换包重试。
 */
function InstallSkillDialog({
  open,
  onOpenChange,
  onInstalled,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onInstalled: () => void
}) {
  const install = useInstallSkill()
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<SkillInstallFailView | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  // 关闭时清理上次的选择与失败展示
  useEffect(() => {
    if (!open) {
      setResult(null)
      setFile(null)
    }
  }, [open])

  const handleInstall = () => {
    if (!file) return
    install.mutate(file, {
      onSuccess: (res) => {
        if (res.installed && res.skill) {
          toast.success(`技能 ${res.skill.name} 已安装并启用`)
          onOpenChange(false)
          setFile(null)
          onInstalled()
        } else {
          setResult({ readiness: res.readiness, findings: res.findings, message: res.message })
        }
      },
      onError: (err) =>
        toast.error('安装失败', {
          description: err instanceof Error ? err.message : '未知错误',
        }),
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* 嵌套弹窗层级高于外层设置弹窗 */}
      <DialogContent className="z-[60] sm:max-w-md">
        <DialogHeader>
          <DialogTitle>安装技能</DialogTitle>
          <DialogDescription>
            选择 .skill 技能包（ZIP 归档，含 SKILL.md）；审查通过后装入你的自定义技能并默认启用，同名技能将被覆盖。
          </DialogDescription>
        </DialogHeader>
        {/* .skill 文件选择：点击卡片区域唤起系统选择器 */}
        <input
          ref={fileRef}
          type="file"
          accept=".skill"
          className="hidden"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null)
            e.target.value = ''
          }}
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          className="flex w-full items-center gap-3 rounded-[12px] border border-dashed border-input px-4 py-3.5 text-left transition-colors hover:border-primary/40 hover:bg-accent/40"
        >
          <Paperclip className="size-4 shrink-0 text-muted-foreground" />
          {file ? (
            <span className="min-w-0 flex-1 truncate text-[13px]">{file.name}</span>
          ) : (
            <span className="text-[13px] text-muted-foreground">点击选择 .skill 文件…</span>
          )}
          {file && (
            <span className="shrink-0 text-[11px] text-muted-foreground">
              {(file.size / 1024 / 1024).toFixed(1)}MB
            </span>
          )}
        </button>
        {/* 审查未通过：内联展示发现清单（不关闭，换包后可重试） */}
        {result && (
          <div className="max-h-[200px] space-y-2 overflow-y-auto rounded-[12px] border border-destructive/30 bg-destructive/5 px-3 py-2.5">
            <div className="text-xs font-medium text-destructive">
              审查未通过（{result.readiness ?? 'unknown'}）{result.message ? `：${result.message}` : ''}
            </div>
            {result.findings.slice(0, 8).map((f, i) => (
              <FindingRow key={`${f.rule_id}-${i}`} finding={f} />
            ))}
            {result.findings.length > 8 && (
              <div className="text-[11px] text-muted-foreground">
                还有 {result.findings.length - 8} 条发现未展示
              </div>
            )}
            {result.findings.length === 0 && (
              <div className="text-[11px] text-muted-foreground">无详细发现，请检查技能包内容。</div>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleInstall} disabled={!file || install.isPending}>
            {install.isPending ? '审查并安装中…' : '审查并安装'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** 安装失败的内联展示形态 */
interface SkillInstallFailView {
  readiness: string | null
  findings: SkillFinding[]
  message: string | null
}

/** 单条审查发现：严重级别着色 + 规则与说明 */
function FindingRow({ finding }: { finding: SkillFinding }) {
  const isError = finding.severity === 'blocker' || finding.severity === 'error'
  return (
    <div className="text-[11px] leading-4">
      <span className={cn('font-mono', isError ? 'text-destructive' : 'text-muted-foreground')}>
        [{finding.severity}] {finding.rule_id}
      </span>
      <span className="text-muted-foreground"> — {finding.message}</span>
    </div>
  )
}

/**
 * 外观面板（AppearancePanel）
 *
 * DeerFlow 式主题卡片：三套背景各一张卡（顶部色板预览 + 名称 + 描述），
 * 点击即切换（由 BackgroundProvider 持久化），当前主题描边高亮。
 */
function AppearancePanel() {
  const { background, setBackground } = useBackground()

  return (
    <>
      <div className="border-b px-6 py-4">
        <div className="text-[15px] font-semibold">外观</div>
        <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
          选择界面背景主题，立即生效并自动记住偏好。
        </p>
      </div>
      <div className="flex-1 overflow-y-auto px-6 py-5">
        <div className="text-xs font-medium text-muted-foreground">主题</div>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
          {BACKGROUNDS.map((b) => {
            const active = background === b.key
            const mock = MOCK[b.key]
            return (
              <button
                key={b.key}
                type="button"
                onClick={() => setBackground(b.key)}
                aria-pressed={active}
                className={cn(
                  'flex flex-col gap-2 rounded-[14px] border p-3.5 text-left transition-all hover:shadow-sm',
                  active
                    ? 'border-primary ring-1 ring-primary'
                    : 'border-input hover:border-primary/40',
                )}
              >
                {/* 1.名称 + 当前选中圆点 */}
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-medium">{b.name}</span>
                  {active && <span className="ml-auto size-1.5 shrink-0 rounded-full bg-primary" />}
                </div>
                {/* 2.描述 */}
                <div className="text-[11px] leading-4 text-muted-foreground">{b.desc}</div>
                {/* 3.迷你窗口预览（色板打底 + 顶栏圆点 + 内容块，占卡片主视觉） */}
                <span
                  className="relative h-28 overflow-hidden rounded-[9px] border border-black/5"
                  style={{ background: SWATCH[b.key] }}
                >
                  <span className="absolute inset-x-2.5 top-2.5 flex items-center gap-1">
                    <span className="size-1.5 rounded-full" style={{ background: mock.line }} />
                    <span className="h-1.5 w-8 rounded-full" style={{ background: mock.line }} />
                    <span className="ml-auto h-1.5 w-5 rounded-full" style={{ background: mock.line }} />
                  </span>
                  <span
                    className="absolute inset-x-2.5 bottom-2.5 top-8 rounded-[6px]"
                    style={{ background: mock.card }}
                  >
                    <span className="absolute left-2 top-2 block h-2 w-9 rounded-full" style={{ background: mock.line }} />
                    <span className="absolute left-2 top-6 block h-1.5 w-14 rounded-full" style={{ background: mock.line }} />
                    <span className="absolute bottom-2 left-2 right-6 block h-1.5 rounded-full" style={{ background: mock.line }} />
                  </span>
                </span>
              </button>
            )
          })}
        </div>
      </div>
    </>
  )
}
