import { api } from '@/core/api/client'
import { type ModelOut } from './types'

export function listModels(): Promise<ModelOut[]> {
  return api.get<ModelOut[]>('/models')
}