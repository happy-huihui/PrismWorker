import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { getSkills, installSkill, saveSkillBlacklist } from './api'
import { type SkillBlacklistPayload } from './types'

export const skillsKey = ['skills'] as const

export function useSkills(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: skillsKey,
    queryFn: getSkills,
    // 未登录时不发请求（避免无意义的 401）
    enabled: options?.enabled ?? true,
  })
}

export function useSaveSkillBlacklist() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: SkillBlacklistPayload) => saveSkillBlacklist(payload),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: skillsKey })
    },
  })
}

export function useInstallSkill() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => installSkill(file),
    // 成功/失败都刷新：失败虽不入库，但保证清单与最新状态一致
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: skillsKey })
    },
  })
}
