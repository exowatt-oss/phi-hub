import { Package, Plus, Search, SearchX } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { useBundles, useProfiles } from '@/api/hooks'
import { BUNDLE_KINDS, type BundleSort } from '@/api/types'
import { BrowseTabs, Chip } from '@/components/BrowseTabs'
import { BundleGrid, BundleGridSkeleton } from '@/components/BundleCard'
import { NewBundleDialog } from '@/components/BundleForm'
import { EmptyState } from '@/components/EmptyState'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { initials } from '@/lib/utils'

/**
 * All bundles — the firehose, sibling tab to the curated Marketplace.
 *
 * Everything anyone in the org has published, personal profiles included,
 * with the full search / kind / sort controls. Defaults to recently
 * updated, because on this page you are looking for what changed rather
 * than for what is blessed.
 */
export function HomePage() {
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [kind, setKind] = useState<string | undefined>()
  const [sort, setSort] = useState<BundleSort>('updated')
  const [creating, setCreating] = useState(false)

  // Debounce the query that reaches the API. `useBundles` keeps the previous
  // page on screen while a new one resolves, so the grid settles rather than
  // strobing on every keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setQ(search.trim()), 250)
    return () => clearTimeout(timer)
  }, [search])

  const bundles = useBundles({ q: q || undefined, kind, sort })
  const profiles = useProfiles()

  const filtered = Boolean(q || kind)
  const results = bundles.data?.bundles ?? []

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 p-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">All bundles</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Everything in the registry — skills, extensions, prompts, themes, instructions,
            settings fragments and model provider catalogs, published under user handles. The{' '}
            <Link to="/" className="underline hover:text-foreground">
              Marketplace
            </Link>{' '}
            tab narrows this to the org-curated set.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <BrowseTabs />
          <Button variant="primary" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" />
            New bundle
          </Button>
        </div>
      </header>

      <div className="flex flex-col gap-3">
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ex-neutral-400"
            aria-hidden="true"
          />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search titles, descriptions and tags"
            aria-label="Search bundles"
            className="pl-9"
          />
        </div>

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

          <div className="ml-auto flex items-center gap-1.5">
            <span className="text-xs text-ex-neutral-400">Sort</span>
            <Chip active={sort === 'updated'} onClick={() => setSort('updated')}>
              Recently updated
            </Chip>
            <Chip active={sort === 'popular'} onClick={() => setSort('popular')}>
              Most installed
            </Chip>
          </div>
        </div>
      </div>

      {bundles.isLoading ? (
        <BundleGridSkeleton />
      ) : bundles.isError ? (
        <EmptyState
          icon={SearchX}
          title="Could not load bundles"
          description="The hub API did not respond. Reload the page to try again."
          action={
            <Button variant="outline" size="sm" onClick={() => void bundles.refetch()}>
              Retry
            </Button>
          }
        />
      ) : results.length === 0 && filtered ? (
        <EmptyState
          icon={SearchX}
          title="No bundles match that"
          description="Try a broader search term, or clear the kind filter."
          action={
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setSearch('')
                setKind(undefined)
              }}
            >
              Clear filters
            </Button>
          }
        />
      ) : results.length === 0 ? (
        <EmptyState
          icon={Package}
          title="The registry is empty"
          description="Nobody has published a bundle yet. Create one here, then push content into it from phi with /hub push."
          action={
            <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
              <Plus className="h-4 w-4" />
              New bundle
            </Button>
          }
        />
      ) : (
        <>
          <div className="text-xs text-ex-neutral-400">
            {bundles.data?.total ?? results.length} bundle
            {(bundles.data?.total ?? results.length) === 1 ? '' : 's'}
          </div>
          <BundleGrid bundles={results} />
        </>
      )}

      <section className="mt-2 flex flex-col gap-3 border-t border-border pt-6">
        <h2 className="text-sm font-bold text-foreground">People</h2>
        {profiles.isLoading ? (
          <div className="flex flex-wrap gap-2">
            {Array.from({ length: 5 }, (_, i) => (
              <Skeleton key={i} className="h-9 w-36 rounded-full" />
            ))}
          </div>
        ) : (profiles.data?.length ?? 0) === 0 ? (
          <p className="text-sm text-muted-foreground">No profiles yet.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {profiles.data?.map((profile) => (
              <Link
                key={profile.handle}
                to={`/p/${profile.handle}`}
                className="flex items-center gap-2 rounded-full border border-border py-1 pl-1 pr-3 text-xs transition-colors hover:border-ex-neutral-300"
              >
                <span className="ex-bg-gradient flex h-7 w-7 items-center justify-center rounded-full text-[10px] font-bold">
                  {initials(profile.display_name || profile.handle)}
                </span>
                <span className="font-bold text-foreground">{profile.handle}</span>
                <span className="text-ex-neutral-400">{profile.bundle_count}</span>
              </Link>
            ))}
          </div>
        )}
      </section>

      {/* Mounted only while open so each open starts from a clean form. */}
      {creating && <NewBundleDialog onClose={() => setCreating(false)} />}
    </div>
  )
}
