import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

/**
 * The bundle `kind` chip. Deliberately monochrome: the palette has exactly one
 * accent (the brand gradient) and eight kinds, so colour-coding them would
 * force off-brand hues. Shape and text carry the meaning instead.
 */
export function KindBadge({ kind, className }: { kind: string; className?: string }) {
  return (
    <Badge variant="muted" className={cn('font-normal tracking-wide', className)}>
      {kind}
    </Badge>
  )
}
