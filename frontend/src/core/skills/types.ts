/** 技能（skills）域类型。 */

/** 技能来源：public = 内置公共技能，custom = 用户自装技能。 */
export type SkillSource = 'public' | 'custom'

/** GET /skills 返回的单个技能；blocked 为当前用户是否已关闭。 */
export interface SkillOut {
  name: string
  description: string
  blocked: boolean
  source: SkillSource
}

/** GET /skills 的响应体。 */
export interface SkillListOut {
  skills: SkillOut[]
}

/** PUT /skills/blacklist 的请求体（全量覆盖，空数组即全部启用）。 */
export interface SkillBlacklistPayload {
  blocked: string[]
}

/** PUT /skills/blacklist 的响应体。 */
export interface SkillBlacklistOut {
  blocked: string[]
}

/** 审查发现（失败时逐条展示）。 */
export interface SkillFinding {
  rule_id: string
  severity: string
  message: string
  path?: string | null
}

/** POST /skills/install 的响应体。 */
export interface SkillInstallOut {
  installed: boolean
  readiness: string | null
  findings: SkillFinding[]
  message: string | null
  skill: { name: string; description: string; path: string } | null
}
