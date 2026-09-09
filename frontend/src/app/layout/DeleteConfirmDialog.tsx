import { useNavigate } from 'react-router-dom'
import { AlertTriangle, Loader2 } from 'lucide-react'
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
import { type ThreadOut } from '@/core/api/types'
import { useDeleteThread } from '@/core/threads'

interface DeleteConfirmDialogProps {
  thread: ThreadOut
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function DeleteConfirmDialog({ thread, open, onOpenChange }: DeleteConfirmDialogProps) {
  const navigate = useNavigate()
  const deleteThread = useDeleteThread()

  const handleDelete = () => {
    deleteThread.mutate(thread.thread_id, {
      onSuccess: () => {
        onOpenChange(false)
        toast.success('会话已删除')
        if (window.location.pathname === `/chats/${thread.thread_id}`) {
          navigate('/')
        }
      },
      onError: (err) =>
        toast.error('删除失败', {
          description: err instanceof Error ? err.message : '未知错误',
        }),
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="size-4 text-destructive" />
            删除会话
          </DialogTitle>
          <DialogDescription>
            将删除「{thread.title}」及其全部聊天记录与产物文件，此操作不可撤销。
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={deleteThread.isPending}>
            取消
          </Button>
          <Button variant="destructive" onClick={handleDelete} disabled={deleteThread.isPending}>
            {deleteThread.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            删除
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}