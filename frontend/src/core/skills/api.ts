import { api } from '@/core/api/client'

import {
  type SkillBlacklistOut,
  type SkillBlacklistPayload,
  type SkillInstallOut,
  type SkillListOut,
} from './types'

/** 查询技能清单（公共 + 用户自定义，含当前用户的 blocked 开关状态）。 */
export function getSkills(): Promise<SkillListOut> {
  return api.get<SkillListOut>('/skills')
}

/** 全量覆盖保存当前用户的技能黑名单（空数组即全部启用）。 */
export function saveSkillBlacklist(payload: SkillBlacklistPayload): Promise<SkillBlacklistOut> {
  return api.put<SkillBlacklistOut>('/skills/blacklist', payload)
}

/** 上传 .skill 归档安装自定义技能（multipart；审查通过默认启用，同名覆盖）。 */
export function installSkill(file: File): Promise<SkillInstallOut> {
  const form = new FormData()
  form.append('file', file)
  return api.post<SkillInstallOut>('/skills/install', form)
}
