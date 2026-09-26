import { type ToolCallItem } from '@/core/runs/useRunStream'

/**
 * 思考链展示助手（buildTimeline）
 *
 * 职责：把工具名 / 工具参数翻成人话——思考链时间线本身已由后端事件按
 *      到达顺序落成（见 core/runs/useRunStream 的 ChainStep），这里不再
 *      从 prints 正则猜顺序。
 *
 * 标题优先级（对齐 DeerFlow）：模型自填的 `description` > 内置中文动作映射 > 原名。
 *   DeerFlow 的思考链卡片标题正是模型在 `tool_calls.args.description` 里自填的
 *   一句话（如「加载前端设计技能来创建足球网站」），比按工具名反查的通用中文
 *   动作名更贴合当下语境，所以只要模型给了就用它。
 *
 * 背景：旧版靠 prints 里的「即将调用工具 X…」锚定工具行、把模型叙述按行
 *      切段，既脆弱（换个措辞就全乱）又把内部进度打印端给用户。
 */

// 工具名 → 中文动作标题（命中关键字即映射；都不中回退原名）
const TOOL_TITLES: Array<[string[], string]> = [
  [['list_uploaded_files'], '列出上传文件'],
  [['present_file', 'present_files'], '展示产物文件'],
  [['read_file', 'get_file'], '读取文件'],
  [['write_file', 'edit_file', 'patch_file', 'update_file'], '写入文件'],
  [['exec_command', 'run_command', 'exec'], '执行命令'],
  [['web_search', 'search'], '网络搜索'],
  [['web_fetch', 'fetch_url', 'browse', 'get_url'], '抓取网页'],
  [['task', 'spawn_task', 'delegate', 'sub_agent'], '派发子代理'],
  [['save_memory', 'remember'], '保存记忆'],
  [['search_memory'], '检索记忆'],
  [['delete_memory'], '删除记忆'],
  [['view_image'], '查看图片'],
  [['activate_skill', 'skill'], '使用技能'],
  [['ask_clarification', 'clarify'], '请求澄清'],
  [['write_todos', 'todo'], '更新任务计划'],
  [['ls', 'list_dir', 'glob', 'grep'], '浏览目录'],
]

/** 工具名 → 内置中文动作标题（不含模型自填标题）。 */
export function toolTitle(tool: string): string {
  for (const [keys, title] of TOOL_TITLES) {
    if (keys.some((k) => tool.includes(k))) return title
  }
  return tool
}

/**
 * 一步工具调用的展示标题。
 * 优先用模型自填的 description（DeerFlow 同款），缺失才回退内置映射。
 */
export function stepTitle(item: ToolCallItem): string {
  const described = (item.description ?? '').trim()
  if (described) return described
  return toolTitle(item.tool)
}

// args 里代表"目标"的关键字段（按优先级取第一个命中的）
const TARGET_KEYS = [
  'path',
  'file_path',
  'filepath',
  'filename',
  'file',
  'command',
  'query',
  'url',
  'key',
  'pattern',
  'skill',
  'skill_name',
  'description',
]

/** 命令类工具：目标要用代码块样式呈现（对齐 DeerFlow 的 bash CodeBlock）。 */
const COMMAND_TOOLS = ['exec_command', 'run_command', 'exec', 'bash', 'shell']

/** 该工具的目标是否应按「命令」渲染（等宽代码块样式）。 */
export function isCommandTool(tool: string): boolean {
  const name = (tool || '').toLowerCase()
  return COMMAND_TOOLS.some((k) => name.includes(k))
}

/** 该工具的目标是否应按「路径」渲染（可点击/等宽的 chip）。 */
const PATH_KEYS = ['path', 'file_path', 'filepath', 'filename', 'file']

/**
 * 从工具参数里抽一个"目标"用于展示（路径 / 命令 / 关键词）。
 *
 * 优先读结构化 `args`（新数据，保留语义键，不会像截断的 JSON 那样丢键名）；
 * 老数据回退到解析 `args_preview`。
 */
export function toolTarget(item: ToolCallItem): string {
  // 1.结构化参数：按语义键优先级取第一个非空字符串
  if (item.args && typeof item.args === 'object') {
    for (const k of TARGET_KEYS) {
      const v = item.args[k]
      if (typeof v === 'string' && v.trim()) return v.trim()
    }
  }
  // 2.兼容回退：解析 args_preview 里的 JSON
  const raw = (item.args_preview || '').trim()
  if (!raw) return ''
  try {
    const obj = JSON.parse(raw)
    if (obj && typeof obj === 'object') {
      for (const k of TARGET_KEYS) {
        const v = (obj as Record<string, unknown>)[k]
        if (typeof v === 'string' && v.trim()) return v.trim()
      }
    }
  } catch {
    // 非 JSON：直接回退原文
  }
  return raw.length > 200 ? raw.slice(0, 200) + '…' : raw
}

/** 目标是否是「路径」类（决定 chip 是否用等宽字体 + 路径图标观感）。 */
export function isPathTarget(item: ToolCallItem): boolean {
  if (item.args && typeof item.args === 'object') {
    return PATH_KEYS.some((k) => typeof item.args?.[k] === 'string' && (item.args[k] as string).trim())
  }
  const raw = (item.args_preview || '').trim()
  if (!raw) return false
  try {
    const obj = JSON.parse(raw) as Record<string, unknown>
    return PATH_KEYS.some((k) => typeof obj[k] === 'string' && (obj[k] as string).trim())
  } catch {
    return false
  }
}

/** 一步的展示标题（工具步用中文动作名；文本步返回空，由正文承担）。 */
export function stepLabel(item: ToolCallItem): string {
  return stepTitle(item)
}
