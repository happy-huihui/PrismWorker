import type { ComponentType } from 'react'
import { MorphIcon } from 'morphicons/react'
import * as L from 'lucide'

/**
 * 图标统一出口（components/icons）
 *
 * 职责：全站图标的唯一来源。底层用 morphicons（图标形变动画库）+ lucide 图标
 *      数据（注意：是 `lucide` 数据包，不是已移除的 `lucide-react` 组件包）。
 * 背景：morphicons 只负责"把一个描边图标形变到另一个"，图标几何来自 lucide 数据。
 *      MorphIcon 是 lucide-react 的直替：透传 size / strokeWidth / className 等
 *      <svg> 属性，静态图标即传一个 icon；需要动效时切换 icon 会自动形变。
 * 约定：为平滑替换旧代码，这里按 lucide-react 的原名再导出一批同名组件；
 *      新组件优先直接用 <MorphIcon icon={...}/> 或下方 toolIcons 语义映射。
 */

// MorphIcon 的入参形状（lucide 的 IconNode：[tag, attrs][]）；对外只当作不透明图标数据
type IconNode = Parameters<typeof MorphIcon>[0]['icon']

export interface IconProps {
  className?: string
  size?: number | string
  strokeWidth?: number
  color?: string
  [key: string]: unknown
}

// 兼容旧代码里的 `type LucideIcon`（图标组件类型）
export type LucideIcon = ComponentType<IconProps>

/** 把一个 lucide 图标数据包装成可直接在 JSX 使用的组件。 */
function makeIcon(node: IconNode): LucideIcon {
  const Component = (props: IconProps) => <MorphIcon icon={node} {...props} />
  Component.displayName = 'MorphIcon'
  return Component
}

// 重新导出底层 MorphIcon，供需要形变/直接传数据的组件使用
export { MorphIcon }
export type { IconNode }

// ── 与 lucide-react 同名的图标 shim（覆盖全站用到的图标）───────────────────
export const Loader2 = makeIcon(L.Loader2)
export const MessageSquarePlus = makeIcon(L.MessageSquarePlus)
export const PanelLeftClose = makeIcon(L.PanelLeftClose)
export const PanelLeftOpen = makeIcon(L.PanelLeftOpen)
export const Sparkles = makeIcon(L.Sparkles)
export const Download = makeIcon(L.Download)
export const FileQuestion = makeIcon(L.FileQuestion)
export const TriangleAlert = makeIcon(L.TriangleAlert)
export const AlertTriangle = makeIcon(L.TriangleAlert) // lucide-react 旧名
export const Check = makeIcon(L.Check)
export const ChevronsUpDown = makeIcon(L.ChevronsUpDown)
export const X = makeIcon(L.X)
export const XIcon = makeIcon(L.X) // lucide-react 旧名
export const Brain = makeIcon(L.Brain)
export const AlertCircle = makeIcon(L.CircleAlert)
export const CheckCircle2 = makeIcon(L.CircleCheck)
export const XCircle = makeIcon(L.CircleX)
export const ArrowLeft = makeIcon(L.ArrowLeft)
export const ArrowUp = makeIcon(L.ArrowUp)
export const ChevronDown = makeIcon(L.ChevronDown)
export const MessagesSquare = makeIcon(L.MessagesSquare)
export const MoreHorizontal = makeIcon(L.MoreHorizontal)
export const Pencil = makeIcon(L.Pencil)
export const Trash2 = makeIcon(L.Trash2)
export const Copy = makeIcon(L.Copy)
export const Paperclip = makeIcon(L.Paperclip)
export const Square = makeIcon(L.Square)
export const File = makeIcon(L.File)
export const Code2 = makeIcon(L.Code2)
export const FileCode2 = makeIcon(L.FileCode2)
export const FileImage = makeIcon(L.FileImage)
export const FileSpreadsheet = makeIcon(L.FileSpreadsheet)
export const FileText = makeIcon(L.FileText)
export const RotateCcw = makeIcon(L.RotateCcw)
export const RefreshCw = makeIcon(L.RefreshCw)
export const Eye = makeIcon(L.Eye)
export const PackageOpen = makeIcon(L.PackageOpen)
export const Bot = makeIcon(L.Bot)
export const Files = makeIcon(L.Files)
export const FolderOpen = makeIcon(L.FolderOpen)
export const Globe = makeIcon(L.Globe)
export const HardDrive = makeIcon(L.HardDrive)
export const Link2 = makeIcon(L.Link2)
export const PenSquare = makeIcon(L.PenSquare)
export const Save = makeIcon(L.Save)
export const Search = makeIcon(L.Search)
export const Terminal = makeIcon(L.Terminal)
export const Users = makeIcon(L.Users)
export const Wrench = makeIcon(L.Wrench)
export const Plus = makeIcon(L.Plus)
export const Send = makeIcon(L.Send)
export const Settings = makeIcon(L.Settings)
export const Palette = makeIcon(L.Palette)
export const Atom = makeIcon(L.Atom)
export const ListTodo = makeIcon(L.ListTodo)
export const BookOpen = makeIcon(L.BookOpen)
export const CircleDot = makeIcon(L.CircleDot)
export const Maximize2 = makeIcon(L.Maximize2)
export const Lock = makeIcon(L.Lock)
export const PanelRight = makeIcon(L.PanelRight)
export const PanelRightClose = makeIcon(L.PanelRightClose)
export const PanelRightOpen = makeIcon(L.PanelRightOpen)
export const ExternalLink = makeIcon(L.ExternalLink)
export const Columns2 = makeIcon(L.Columns2)
export const Route = makeIcon(L.Route)
export const Globe2 = makeIcon(L.Globe)
export const Wand2 = makeIcon(L.Wand2)
export const ImagePlus = makeIcon(L.ImagePlus)
export const ChevronUp = makeIcon(L.ChevronUp)

// ── 模板自绘图标（取自 explore/index.html 的内联 SVG，与模板观感 1:1）───────

/** 模板「atom 原子轨道」：品牌标识（登录框脚注）用，六瓣轨道更饱满 */
export const AtomTemplate: IconNode = [
  ['circle', { cx: 12, cy: 12, r: 1.7, fill: 'currentColor', stroke: 'none' }],
  ['ellipse', { cx: 12, cy: 12, rx: 10, ry: 4.4 }],
  ['ellipse', { cx: 12, cy: 12, rx: 10, ry: 4.4, transform: 'rotate(60 12 12)' }],
  ['ellipse', { cx: 12, cy: 12, rx: 10, ry: 4.4, transform: 'rotate(120 12 12)' }],
]

/** 思考（原子）：两条交叉花瓣轨道 + 实心核（小尺寸下比空心圆更清晰），供思考药丸 / 思考链头部共用 */
export const ThinkAtom: IconNode = [
  ['circle', { cx: 12, cy: 12, r: 1.6, fill: 'currentColor', stroke: 'none' }],
  ['path', { d: 'M20.2 20.2c2.04-2.03.02-7.36-4.5-11.9-4.54-4.52-9.87-6.54-11.9-4.5-2.04 2.03-.02 7.36 4.5 11.9 4.54 4.52 9.87 6.54 11.9 4.5Z' }],
  ['path', { d: 'M15.7 15.7c4.52-4.54 6.54-9.87 4.5-11.9-2.03-2.04-7.36-.02-11.9 4.5-4.52 4.54-6.54 9.87-4.5 11.9 2.03 2.04 7.36.02 11.9-4.5Z' }],
]

/** 设置（六齿齿轮 + 中心孔）：侧栏底部设置入口 */
export const SettingsGear: IconNode = L.Settings

/** 侧栏折叠（模板品牌行右侧：左面板 + 向右箭头） */
const PanelCollapse: IconNode = [
  ['path', { d: 'M15 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h9M11 12l3-3M11 12l3 3' }],
]

/** 侧栏展开（镜像） */
const PanelExpand: IconNode = [
  ['path', { d: 'M9 4h9a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H9M13 12l-3-3M13 12l-3 3' }],
]

/** 背景调色板（模板底部：带三点的半月调色板） */
const PaletteLine: IconNode = [
  ['path', { d: 'M12 3a9 9 0 1 0 0 18c1 0 1.6-.8 1.6-1.6 0-.5-.2-.8-.5-1.1-.3-.3-.5-.7-.5-1.1 0-.9.7-1.6 1.6-1.6H16a5 5 0 0 0 5-5c0-3.9-4-6.6-9-6.6Z' }],
  ['circle', { cx: 7.5, cy: 10.5, r: 1 }],
  ['circle', { cx: 12, cy: 7.5, r: 1 }],
  ['circle', { cx: 16.5, cy: 10.5, r: 1 }],
]

/** 技能/文档加载（模板思考链：开卷书） */
const SkillBook: IconNode = [
  ['path', { d: 'M12 6.5C10.5 5 8 4.5 4 5v13c4-.5 6.5 0 8 1.5 1.5-1.5 4-2 8-1.5V5c-4-.5-6.5 0-8 1.5z' }],
  ['path', { d: 'M12 6.5v13' }],
]

/** 创建/写入文件（模板思考链：文档 + 钢笔） */
const WriteDoc: IconNode = [
  ['path', { d: 'M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h5' }],
  ['path', { d: 'M13 3v5h5' }],
  ['path', { d: 'M20.7 13.3a1 1 0 0 0-1.4 0l-4.3 4.3-.4 1.7 1.7-.4 4.3-4.3a1 1 0 0 0 0-1.4z' }],
]

export { PanelCollapse, PanelExpand, PaletteLine, SkillBook, WriteDoc }
export { AtomTemplate as AtomMark }

/**
 * 思考链工具图标映射（tool name → 语义图标数据）
 *
 * 依据工具名里的关键字，选一个"自然、见名知意"的描边图标，供 ToolRow 渲染。
 * 命中顺序即优先级；都不中则回退到扳手（通用工具）。
 */
const TOOL_ICON_RULES: Array<[string[], IconNode]> = [
  [['read_file', 'get_file', 'read'], L.FileText],
  [['write_file', 'edit_file', 'patch_file', 'update_file', 'create'], WriteDoc],
  [['exec_command', 'run_command', 'exec', 'bash', 'shell'], L.Terminal],
  [['web_search', 'search'], L.Search],
  [['web_fetch', 'fetch_url', 'browse', 'get_url', 'url'], L.Globe],
  [['list_dir', 'glob', 'grep', 'list_files', 'directory', 'dir'], L.FolderOpen],
  [['list_uploaded', 'upload', 'file'], L.Files],
  [['present_file', 'present', 'artifact', 'download', 'package'], L.PackageOpen],
  [['task', 'spawn', 'delegate', 'sub_agent', 'subagent'], L.Bot],
  [['save_memory', 'search_memory', 'delete_memory', 'memory', 'remember'], L.Brain],
  [['skill', 'activate'], SkillBook],
  [['view_image', 'image'], L.FileImage],
  [['generate_image', 'draw_image', 'text2image', 't2i'], L.ImagePlus],
  [['clarify', 'ask'], L.MessagesSquare],
  [['todo', 'plan'], L.ListTodo],
]

/** 按工具名解析语义图标数据（供 <MorphIcon icon={...}/> 使用）。 */
export function resolveToolIcon(tool: string): IconNode {
  const name = (tool || '').toLowerCase()
  for (const [keys, node] of TOOL_ICON_RULES) {
    if (keys.some((k) => name.includes(k))) return node
  }
  return L.Wrench
}
