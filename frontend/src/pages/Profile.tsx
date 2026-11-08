import { Package, Plus, Store, UserX, X } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import {
  useAddPublisher,
  useOrgPublishers,
  useProfile,
  useRemovePublisher,
} from '@/api/hooks'
import type { Profile, Publisher } from '@/api/types'
import { BundleGrid, BundleGridSkeleton } from '@/components/BundleCard'
import { ErrorNote } from '@/components/BundleForm'
import { EmptyState } from '@/components/EmptyState'
import { absoluteTime, monthYear, relativeTime } from '@/components/format'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { initials } from '@/lib/utils'

/**
 * Profile header. Shared with My hub so a user's own page looks like everyone
 * else's — the gradient avatar is the one brand accent on the surface.
 */
export function ProfileHeader({
  profile,
  children,
}: {
  profile: Profile
  children?: ReactNode
}) {
  return (
    <header className="flex flex-wrap items-center gap-4 rounded-lg border border-border bg-card p-6">
      <span className="ex-bg-gradient flex h-16 w-16 shrink-0 items-center justify-center rounded-full text-xl font-bold">
        {initials(profile.display_name || profile.handle)}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="truncate text-2xl font-bold tracking-tight text-foreground">
            {profile.display_name || profile.handle}
          </h1>
          {profile.is_org && (
            <Badge variant="outline" className="gap-1 font-normal">
              <Store className="h-3 w-3" aria-hidden="true" />
              org
            </Badge>
          )}
        </div>
        <p className="truncate text-sm text-muted-foreground">
          <span className="font-mono">{profile.handle}</span>
          {/* An org's address is a placeholder for the NOT NULL column, not
              a mailbox anyone reads — showing it would just invite mail. */}
          {!profile.is_org && <> · {profile.email}</>}
        </p>
        <p className="mt-1 text-xs text-ex-neutral-400">
          <strong className="font-bold text-ex-neutral-500">{profile.bundle_count}</strong> bundle
          {profile.bundle_count === 1 ? '' : 's'} ·{' '}
          <strong className="font-bold text-ex-neutral-500">{profile.import_count}</strong> import
          {profile.import_count === 1 ? '' : 's'} · member since {monthYear(profile.created_at)}
        </p>
      </div>
      {children}
    </header>
  )
}

/* --------------------------------------------------------------- publishers */

function PublisherRow({
  publisher,
  orgHandle,
  canManage,
  removable,
}: {
  publisher: Publisher
  orgHandle: string
  canManage: boolean
  removable: boolean
}) {
  const remove = useRemovePublisher(orgHandle)
  const [confirming, setConfirming] = useState(false)

  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-sm">
      <span className="ex-bg-gradient flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[10px] font-bold">
        {initials(publisher.display_name || publisher.handle)}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link to={`/p/${publisher.handle}`} className="truncate font-bold hover:underline">
            {publisher.display_name || publisher.handle}
          </Link>
          {publisher.pending && (
            <Badge variant="outline" className="font-normal" title="Invited, but has never signed in">
              invited
            </Badge>
          )}
        </div>
        <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-ex-neutral-400">
          <code className="font-mono">{publisher.handle}</code>
          <span title={absoluteTime(publisher.added_at)}>
            added {relativeTime(publisher.added_at)}
            {publisher.added_by ? ` by ${publisher.added_by}` : ''}
          </span>
        </div>
      </div>

      {canManage &&
        (confirming ? (
          <span className="flex items-center gap-2">
            <span className="text-xs text-ex-neutral-500">Remove?</span>
            <Button
              variant="destructive"
              size="sm"
              disabled={remove.isPending}
              onClick={() => remove.mutate(publisher.handle)}
            >
              Yes
            </Button>
            <Button variant="outline" size="sm" onClick={() => setConfirming(false)}>
              No
            </Button>
          </span>
        ) : (
          <Button
            variant="ghost"
            size="icon"
            // The server enforces the >=1 invariant with a 409; disabling
            // the control means the last publisher never has to discover
            // that by being refused.
            disabled={!removable}
            title={
              removable
                ? `Remove ${publisher.handle}`
                : 'An org must keep at least one publisher — add a replacement first.'
            }
            onClick={() => setConfirming(true)}
          >
            <X className="h-4 w-4" />
            <span className="sr-only">Remove {publisher.handle}</span>
          </Button>
        ))}
    </li>
  )
}

function AddPublisherForm({ orgHandle }: { orgHandle: string }) {
  const add = useAddPublisher(orgHandle)
  const [email, setEmail] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    const value = email.trim()
    if (!value) return
    add.mutate(value, { onSuccess: () => setEmail('') })
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="colleague@example.com"
          aria-label="Publisher email"
          className="h-9 max-w-xs"
        />
        <Button type="submit" variant="outline" size="sm" disabled={add.isPending}>
          <Plus className="h-3.5 w-3.5" />
          {add.isPending ? 'Adding…' : 'Add publisher'}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        They do not need to have used the hub before — a profile is created for them, and the grant
        is live immediately.
      </p>
      {add.isError && <ErrorNote>{(add.error as Error).message}</ErrorNote>}
    </form>
  )
}

/**
 * An org's publisher roster. Read by anyone (knowing who stands behind a
 * bundle is the point of the org handle); editable only by the publishers
 * themselves, which the server tells us via `is_publisher` rather than the
 * page re-deriving the rule.
 */
function PublishersSection({ orgHandle }: { orgHandle: string }) {
  const roster = useOrgPublishers(orgHandle)

  if (roster.isLoading) return <Skeleton className="h-32 w-full" />
  if (roster.isError || !roster.data) return null

  const { publishers, is_publisher } = roster.data

  return (
    <section className="flex flex-col gap-3">
      <div>
        <h2 className="text-sm font-bold text-foreground">Publishers</h2>
        <p className="text-xs text-muted-foreground">
          Who may publish, edit and delete under <code className="font-mono">{orgHandle}/</code>.
          This is the trust boundary for everything on the Marketplace tab.
        </p>
      </div>

      <ul className="divide-y divide-border rounded-lg border border-border">
        {publishers.map((publisher) => (
          <PublisherRow
            key={publisher.handle}
            publisher={publisher}
            orgHandle={orgHandle}
            canManage={is_publisher}
            removable={publishers.length > 1}
          />
        ))}
      </ul>

      {is_publisher && <AddPublisherForm orgHandle={orgHandle} />}
    </section>
  )
}

/* --------------------------------------------------------------------- page */

export function ProfilePage() {
  const { handle } = useParams<{ handle: string }>()
  const profile = useProfile(handle)

  if (profile.isLoading) {
    return (
      <div className="mx-auto flex max-w-6xl flex-col gap-6 p-8">
        <Skeleton className="h-28 w-full" />
        <BundleGridSkeleton count={3} />
      </div>
    )
  }

  if (profile.isError) {
    const notFound = profile.error instanceof ApiError && profile.error.status === 404
    return (
      <div className="mx-auto max-w-2xl p-8">
        <EmptyState
          icon={UserX}
          title={notFound ? 'No such profile' : 'Could not load this profile'}
          description={
            notFound
              ? `Nobody publishes under "${handle}". Profiles are created on first touch, so a handle only exists once its owner has used the hub.`
              : (profile.error as Error).message
          }
          action={
            <Button variant="outline" size="sm" asChild>
              <Link to="/">Back to browse</Link>
            </Button>
          }
        />
      </div>
    )
  }

  if (!profile.data) return null

  const isOrg = profile.data.profile.is_org

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-8 p-8">
      <ProfileHeader profile={profile.data.profile} />

      {isOrg && <PublishersSection orgHandle={profile.data.profile.handle} />}

      <section className="flex flex-col gap-3">
        {isOrg && <h2 className="text-sm font-bold text-foreground">Published bundles</h2>}
        {profile.data.bundles.length === 0 ? (
          <EmptyState
            icon={Package}
            title="No bundles published"
            description={`${profile.data.profile.handle} has not published anything yet.`}
          />
        ) : (
          <BundleGrid bundles={profile.data.bundles} showInstall={isOrg} />
        )}
      </section>
    </div>
  )
}
