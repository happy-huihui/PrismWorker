import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { type ThreadOut } from '@/core/api/types'
import { useRenameThread } from '@/core/threads'

interface RenameDialogProps {
  thread: ThreadOut
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function RenameDialog({ thread, open, onOpenChange }: RenameDialogProps) {
  const renameThread = useRenameThread()
  const [title, setTitle] = useState(thread.title)

  useEffect(() => {
    if (open) setTitle(thread.title)
  }, [open, thread.title])

  const handleSubmit = () => {
    const next = title.trim()
    if (!next) return
    renameThread.mutate(
      { threadId: thread.thread_id, payload: { title: next } },
      {
        onSuccess: () => {
          onOpenChange(false)
          toast.success('已重命名')
        },
        onError: (err) =>
          toast.error('重命名失败', {
            description: err instanceof Error ? err.message : '未知错误',
          }),
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>重命名会话</DialogTitle>
          <DialogDescription>输入新的会话标题。</DialogDescription>
        </DialogHeader>
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSubmit()
            }
          }}
          placeholder="会话标题"
          autoFocus
          maxLength={80}
        />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleSubmit} disabled={renameThread.isPending || !title.trim()}>
            {renameThread.isPending ? '保存中…' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}