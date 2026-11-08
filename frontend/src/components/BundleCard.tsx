import { Check, Copy, Download } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import type { BundleSummary } from '@/api/types'
import { relativeTime, absoluteTime } from '@/components/format'
import { KindBadge } from '@/components/KindBadge'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { copyText, installCommand } from '@/lib/hub'

/**
 * One-click copy of a card's install command.
 *
 * `relative z-10` is load-bearing: the card's title link stretches a
 * pseudo-element over the whole card, and without its own stacking context
 * this button would sit underneath it and navigate instead of copying.
 */
function InstallButton({ bundle }: { bundle: BundleSummary }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const command = installCommand(bundle)

  useEffect(() => () => clearTimeout(timer.current), [])

  return (
    <button
      type="button"
      onClick={async () => {
        const ok = await copyText(command)
        setCopied(ok)
        clearTimeout(timer.current)
        timer.current = setTimeout(() => setCopied(false), 2000)
      }}
      title={`Copy “${command}”`}
      aria-label={`Copy install command for ${bundle.owner}/${bundle.slug}`}
      className="relative z-10 flex w-full items-center gap-2 rounded-md border border-border bg-ex-neutral-100 px-2.5 py-1.5 text-left font-mono text-[11px] text-ex-neutral-700 transition-colors hover:border-ex-neutral-300 hover:bg-background"
    >
      <span className="min-w-0 flex-1 truncate">{command}</span>
      {copied ? (
        <Check className="h-3.5 w-3.5 shrink-0 text-ex-neutral-500" aria-hidden="true" />
      ) : (
        <Copy className="h-3.5 w-3.5 shrink-0 text-ex-neutral-400" aria-hidden="true" />
      )}
      <span className="sr-only">{copied ? 'Copied' : 'Copy'}</span>
    </button>
  )
}

/**
 * One bundle in a grid. Shared by Marketplace, All bundles, Profile and My
 * hub so a bundle looks identical wherever it turns up.
 *
 * The whole card is clickable via a stretched pseudo-element on the title link
 * rather than by wrapping the card in an anchor: the owner handle is its own
 * link, and nesting anchors is invalid HTML that browsers silently unnest.
 *
 * `showInstall` adds the copy-the-command affordance. The marketplace turns
 * it on because taking a curated bundle is the point of that page; the
 * other grids leave it off so a list of your own drafts is not covered in
 * install buttons.
 */
export function BundleCard({
  bundle,
  showInstall = false,
}: {
  bundle: BundleSummary
  showInstall?: boolean
}) {
  return (
    <div className="group relative flex flex-col gap-2.5 rounded-lg border border-border bg-card p-4 transition-colors hover:border-ex-neutral-300">
      <div className="flex items-start justify-between gap-2">
        <Link
          to={`/b/${bundle.owner}/${bundle.slug}`}
          className="truncate text-sm font-bold text-foreground after:absolute after:inset-0 after:content-['']"
        >
          {bundle.title || bundle.slug}
        </Link>
        <KindBadge kind={bundle.kind} className="shrink-0" />
      </div>

      <div className="relative z-10 truncate font-mono text-xs text-muted-foreground">
        <Link to={`/p/${bundle.owner}`} className="hover:text-foreground hover:underline">
          {bundle.owner}
        </Link>
        <span aria-hidden="true">/</span>
        <span>{bundle.slug}</span>
      </div>

      <p className="line-clamp-2 min-h-8 text-xs leading-4 text-ex-neutral-500">
        {bundle.description || 'No description.'}
      </p>

      {bundle.tags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {bundle.tags.slice(0, 4).map((tag) => (
            <Badge key={tag} variant="outline" className="px-2 py-0 text-[11px] font-normal">
              {tag}
            </Badge>
          ))}
          {bundle.tags.length > 4 && (
            <span className="text-[11px] text-ex-neutral-400">+{bundle.tags.length - 4}</span>
          )}
        </div>
      )}

      <div className="mt-auto flex items-center gap-3 pt-1 text-xs text-ex-neutral-400">
        <span className="inline-flex items-center gap-1">
          <Download className="h-3 w-3" aria-hidden="true" />
          {bundle.install_count}
        </span>
        {bundle.latest_version > 0 ? (
          <span>v{bundle.latest_version}</span>
        ) : (
          <span>unpublished</span>
        )}
        <span className="ml-auto truncate" title={absoluteTime(bundle.updated_at)}>
          updated {relativeTime(bundle.updated_at)}
        </span>
      </div>

      {showInstall && bundle.latest_version > 0 && <InstallButton bundle={bundle} />}
    </div>
  )
}

/** Grid placeholder — skeletons keep the layout from jumping on first paint. */
export function BundleCardSkeleton() {
  return (
    <div className="flex flex-col gap-2.5 rounded-lg border border-border bg-card p-4">
      <Skeleton className="h-4 w-2/3" />
      <Skeleton className="h-3 w-1/2" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-4/5" />
      <Skeleton className="mt-2 h-3 w-1/3" />
    </div>
  )
}

export function BundleGrid({
  bundles,
  showInstall = false,
}: {
  bundles: BundleSummary[]
  showInstall?: boolean
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {bundles.map((bundle) => (
        <BundleCard key={bundle.id} bundle={bundle} showInstall={showInstall} />
      ))}
    </div>
  )
}

export function BundleGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: count }, (_, i) => (
        <BundleCardSkeleton key={i} />
      ))}
    </div>
  )
}
