import { PackageOpen, SearchX, Store } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { useBundles } from '@/api/hooks'
import { BUNDLE_KINDS } from '@/api/types'
import { BrowseTabs, Chip } from '@/components/BrowseTabs'
import { BundleGrid, BundleGridSkeleton } from '@/components/BundleCard'
import { EmptyState } from '@/components/EmptyState'
import { Button } from '@/components/ui/button'

/**
 * The landing page: the org-curated slice of the registry.
 *
 * Everything here is owned by an ORG profile, which means a named set of
 * publishers decided it should carry the org's handle — the same trust
 * model as a Claude plugin marketplace, and the reason phi auto-updates
 * these by default while only notifying about personal bundles.
 *
 * Sorted popular-first on purpose. "Recently updated" is the right default
 * for the firehose (All bundles), where you are looking for what changed;
 * on a curated shelf you are looking for what people actually use.
 */
export function MarketplacePage() {
  const [kind, setKind] = useState<string | undefined>()
  const bundles = useBundles({ org_only: true, kind, sort: 'popular' })

  const results = bundles.data?.bundles ?? []
  const total = bundles.data?.total ?? results.length

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 p-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Marketplace</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Curated phi configuration published under a configured org handle. A named set of
            publishers gates what ships here, so installs track the latest version and phi keeps
            them current for you.
          </p>
        </div>
        <BrowseTabs />
      </header>

      <div className="flex flex-wrap items-center gap-1.5">
        <Chip active={!kind} onClick={() => setKind(undefined)}>
          All
        </Chip>
        {BUNDLE_KINDS.map((k) => (
          <Chip
            key={k}
            active={kind === k}
            // Clicking the active chip clears it — a single-select filter
            // with no visible "clear" control has to be self-undoing.
            onClick={() => setKind(kind === k ? undefined : k)}
          >
            {k}
          </Chip>
        ))}
        <span className="ml-auto text-xs text-ex-neutral-400">most installed first</span>
      </div>

      {bundles.isLoading ? (
        <BundleGridSkeleton />
      ) : bundles.isError ? (
        <EmptyState
          icon={SearchX}
          title="Could not load the marketplace"
          description="The hub API did not respond. Reload the page to try again."
          action={
            <Button variant="outline" size="sm" onClick={() => void bundles.refetch()}>
              Retry
            </Button>
          }
        />
      ) : results.length === 0 && kind ? (
        <EmptyState
          icon={SearchX}
          title={`Nothing published as “${kind}” yet`}
          description="Clear the filter to see the rest of the marketplace, or browse everything the org has published."
          action={
            <Button variant="outline" size="sm" onClick={() => setKind(undefined)}>
              Clear filter
            </Button>
          }
        />
      ) : results.length === 0 ? (
        <EmptyState
          icon={Store}
          title="The marketplace is empty"
          description="Nothing has been published under an org handle yet. If you are one of the org's publishers, the New bundle button on the All bundles tab lets you publish as the org; everything else in the registry is already over there."
          action={
            <Button variant="outline" size="sm" asChild>
              <Link to="/browse">Browse all bundles</Link>
            </Button>
          }
        />
      ) : (
        <>
          <div className="text-xs text-ex-neutral-400">
            {total} bundle{total === 1 ? '' : 's'}
          </div>
          <BundleGrid bundles={results} showInstall />
        </>
      )}

      <section className="mt-2 flex items-start gap-3 rounded-lg border border-dashed border-border px-4 py-3 text-xs text-muted-foreground">
        <PackageOpen className="mt-0.5 h-4 w-4 shrink-0 text-ex-neutral-400" aria-hidden="true" />
        <p>
          Every install lands in phi&apos;s isolated bundle store and links in as a single{' '}
          <code className="rounded bg-ex-neutral-100 px-1 font-mono">packages</code> entry — your own
          skills, extensions and settings are never touched. Pin a version instead with{' '}
          <code className="font-mono">/hub import owner/slug@v2</code>, and it stops moving.
        </p>
      </section>
    </div>
  )
}
