import { Check, Copy } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { copyText } from '@/lib/hub'

interface CopyBlockProps {
  /** Short heading above the snippet, e.g. "Tell your phi". */
  label: string
  /** One line on when to reach for this snippet rather than the others. */
  hint?: string
  value: string
  /** Fired only on a successful copy — the caller uses it for telemetry. */
  onCopied?: () => void
}

export function CopyBlock({ label, hint, value, onCopied }: CopyBlockProps) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => () => clearTimeout(timer.current), [])

  async function handleCopy() {
    const ok = await copyText(value)
    if (ok) onCopied?.()
    setState(ok ? 'copied' : 'failed')
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setState('idle'), 2000)
  }

  return (
    <div className="rounded-lg border border-border bg-background">
      <div className="flex items-start justify-between gap-3 border-b border-border px-3 py-2">
        <div className="min-w-0">
          <div className="text-xs font-bold uppercase tracking-wider text-ex-neutral-500">
            {label}
          </div>
          {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
        </div>
        <Button variant="outline" size="sm" onClick={handleCopy} className="shrink-0">
          {state === 'copied' ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          {state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : 'Copy'}
        </Button>
      </div>
      <pre className="overflow-x-auto px-3 py-2.5 font-mono text-xs leading-relaxed text-ex-neutral-700">
        {value}
      </pre>
    </div>
  )
}
