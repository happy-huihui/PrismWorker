import { Skill } from '@/components/icons'
import { cn } from '@/lib/utils'
import { type SkillOut } from '@/core/skills'

/**
 * 斜杠技能浮层（SkillSlashMenu）
 *
 * 职责：输入框内输入 / 时的技能候选列表（纯展示）——WorkBuddy 式单行条目：
 *      图标 + 技能名 + 单行截断描述；↑↓ 高亮、Enter 选中、点击选中。
 * 定位：由 ChatInput 的输入卡片（relative 锚点）绝对定位在卡片上方，
 *      与输入卡片等宽对齐；列表超高时内部滚动（滚轮下滑查看全部）。
 */

interface SkillSlashMenuProps {
  /** 候选技能（已过滤未拉黑 + 已按查询词过滤） */
  skills: SkillOut[]
  /** 当前高亮索引 */
  activeIndex: number
  /** 鼠标移入项时同步高亮（键盘/鼠标一致） */
  onHover: (index: number) => void
  /** 选中技能（点击） */
  onSelect: (name: string) => void
}

export function SkillSlashMenu({ skills, activeIndex, onHover, onSelect }: SkillSlashMenuProps) {
  // 候选已按「公共在前、私有在后」排序；按 source 切两组，无私有组则不显示其标题
  const publicCount = skills.filter((s) => s.source === 'public').length
  const privateCount = skills.length - publicCount

  const renderItem = (skill: SkillOut, index: number) => (
    <button
      key={skill.name}
      type="button"
      role="option"
      aria-selected={index === activeIndex}
      onMouseEnter={() => onHover(index)}
      onClick={() => onSelect(skill.name)}
      className={cn(
        'flex w-full items-center gap-2.5 rounded-[10px] px-3 py-2 text-left transition-colors',
        index === activeIndex ? 'bg-primary/10' : 'hover:bg-secondary',
      )}
    >
      {/* 1.技能图标 */}
      <Skill className="size-4 shrink-0 text-muted-foreground" />
      {/* 2.技能名 */}
      <span
        className={cn(
          'shrink-0 text-sm font-medium',
          index === activeIndex && 'text-primary',
        )}
      >
        /{skill.name}
      </span>
      {/* 3.单行截断描述 */}
      {skill.description && (
        <span className="min-w-0 flex-1 truncate text-[13px] text-muted-foreground">
          {skill.description}
        </span>
      )}
    </button>
  )

  const groupLabel = (label: string, count: number) => (
    <div className="px-3 pb-1 pt-2 text-[11px] text-muted-foreground first:pt-1">
      {label} ({count})
    </div>
  )

  return (
    <div
      role="listbox"
      aria-label="技能列表"
      className="absolute bottom-full left-0 z-50 mb-2 max-h-[320px] w-full overflow-y-auto rounded-[14px] border border-input bg-card p-1.5 shadow-[0_2px_4px_rgba(60,45,20,.06),0_16px_40px_rgba(60,45,20,.16)]"
    >
      {/* 公共组 */}
      {publicCount > 0 && (
        <>
          {groupLabel('公共', publicCount)}
          {skills.slice(0, publicCount).map((skill, i) => renderItem(skill, i))}
        </>
      )}
      {/* 私有组（无自定义技能则整组不显示） */}
      {privateCount > 0 && (
        <>
          {groupLabel('私有', privateCount)}
          {skills.slice(publicCount).map((skill, i) => renderItem(skill, publicCount + i))}
        </>
      )}
    </div>
  )
}
