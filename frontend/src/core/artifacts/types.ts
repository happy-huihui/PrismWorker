import { type RunOut } from '@/core/api/types'

export type ArtifactPath = string

export interface ArtifactItem {
  /** 完整虚拟路径（/mnt/user-data/outputs/...） */
  virtualPath: ArtifactPath
  /** 相对 outputs 的路径（下载 URL 的 path 段） */
  relPath: string
  /** 文件名（最后一段） */
  filename: string
}

export function artifactsFromRun(run: RunOut | null | undefined): ArtifactPath[] {
  return run?.artifacts ?? []
}

export type { RunOut }