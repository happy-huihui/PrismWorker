import {
  File,
  FileCode2,
  FileImage,
  FileSpreadsheet,
  FileText,
  type LucideIcon,
} from '@/components/icons'

import { isHtmlArtifact } from '@/core/artifacts/preview'
import { artifactKind } from '@/core/artifacts/utils'

/**
 * 产物「类别」的展示元数据（kindMeta）
 *
 * 职责：把 artifactKind() 给出的类别，映射成前端要用的图标与中文名。
 *      单独成模块是因为「类别 → 图标/文案」是纯展示知识，
 *      会被头部选择器、消息流卡片等多处复用，不该绑在某一个组件里。
 *
 * 注意 HTML 单独判一层：artifactKind 把 .html 归到 code，
 * 但对用户来说它是「网页」（有独立预览能力），不是普通代码文件。
 */

const KIND_ICONS: Record<ReturnType<typeof artifactKind>, LucideIcon> = {
  image: FileImage,
  markdown: FileText,
  code: FileCode2,
  text: FileSpreadsheet,
  other: File,
}

/** 该产物在界面上用什么图标表示。 */
export function kindIcon(path: string): LucideIcon {
  return KIND_ICONS[artifactKind(path)]
}

/** 该产物的中文类别名（用于副标题 / 下拉项说明）。 */
export function kindLabel(path: string): string {
  if (isHtmlArtifact(path)) return '网页'
  switch (artifactKind(path)) {
    case 'image':
      return '图片'
    case 'markdown':
      return '文档'
    case 'code':
      return '代码'
    case 'text':
      return '文本'
    default:
      return '文件'
  }
}
