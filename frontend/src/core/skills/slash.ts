import { type SkillOut } from './types'

/**
 * 斜杠技能改写（slash）——把用户以 /技能名 开头的输入，在发送前改写为
 * 自然语言提示（方案 A）：零后端契约变更，激活仍由模型读到技能清单后自主完成。
 */

/** 匹配输入开头的 /技能名（后跟空格或整句结束）；技能名只允许字母数字连字符。 */
const SLASH_SKILL_RE = /^\/([a-z0-9]+(?:-[a-z0-9]+)*)(?:\s+([\s\S]*))?$/i

/**
 * 改写以 /技能名 开头的输入为「请使用技能」提示词。
 *
 * 参数：
 *   text    用户输入（已 trim）
 *   skills  技能清单（用于校验技能名真实存在；未加载时不改写）
 *
 * 返回：
 *   改写后的文本；不是 /技能名 形态或技能不存在时原样返回
 */
export function applySkillSlash(text: string, skills: SkillOut[] | undefined): string {
  const match = SLASH_SKILL_RE.exec(text)
  // 1.不是 /技能名 形态，原样返回
  if (!match) return text
  const name = match[1]
  const task = (match[2] ?? '').trim()
  // 2.技能清单未加载或技能不存在，原样返回（避免把普通文本误改写）
  if (!skills?.some((s) => s.name.toLowerCase() === name.toLowerCase())) return text
  // 3.有任务：提示词 + 任务；无任务：仅请求使用技能
  return task
    ? `请使用 ${name} 技能完成以下任务：\n\n${task}`
    : `请使用 ${name} 技能。`
}
