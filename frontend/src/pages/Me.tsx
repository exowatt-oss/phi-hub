import { Check, KeyRound, Package, Pencil, Plus, X } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import {
  useMintToken,
  useMyOverview,
  useProfile,
  useRevokeToken,
  useUpdateDisplayName,
} from '@/api/hooks'
import type { ApiToken } from '@/api/types'
import { BundleGrid, BundleGridSkeleton } from '@/components/BundleCard'
import { ErrorNote, NewBundleDialog } from '@/components/BundleForm'
import { CopyBlock } from '@/components/CopyBlock'
import { EmptyState } from '@/components/EmptyState'
import { absoluteTime, relativeTime } from '@/components/format'
import { ProfileHeader } from '@/pages/Profile'
import { Badge } from '@/components/ui/badge'
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
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'

/* --------------------------------------------------------------- mint flow */

/**
 * Mint dialog. Two states in one component because they are one flow: the
 * name prompt, then the single reveal of the plaintext token.
 */
function MintTokenDialog({ onClose }: { onClose: () => void }) {
  const mint = useMintToken()
  const [name, setName] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    mint.mutate(name.trim() || 'manual token')
  }

  const created = mint.data

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        {created ? (
          <>
            <DialogHeader>
              <DialogTitle>Copy your token now</DialogTitle>
              <DialogDescription>
                The hub stores only a hash of this value. Once you close this dialog there is no
                way to see it again — mint a replacement if you lose it.
              </DialogDescription>
            </DialogHeader>

            <CopyBlock
              label={created.api_token.name}
              hint="Put it in ~/.pi/agent/hub.json as `token`, or export it as $PHI_HUB_TOKEN."
              value={created.token}
            />

            <DialogFooter>
              <Button variant="primary" onClick={onClose}>
                Done
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>New token</DialogTitle>
              <DialogDescription>
                phi normally gets its token through the device flow —{' '}
                <code className="font-mono">/hub login</code>, then approve it on the Connect page.
                This is the manual fallback for scripts and for machines that cannot open a browser.
              </DialogDescription>
            </DialogHeader>

            <form onSubmit={submit} className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="token-name">Name</Label>
                <Input
                  id="token-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="ci-publisher"
                  autoFocus
                />
                <p className="text-xs text-muted-foreground">
                  Only for your own bookkeeping — it is what the token list shows.
                </p>
              </div>

              {mint.isError && <ErrorNote>{(mint.error as Error).message}</ErrorNote>}

              <DialogFooter>
                <Button type="button" variant="outline" onClick={onClose}>
                  Cancel
                </Button>
                <Button type="submit" variant="primary" disabled={mint.isPending}>
                  {mint.isPending ? 'Minting…' : 'Mint token'}
                </Button>
              </DialogFooter>
            </form>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

/* ------------------------------------------------------------- token rows */

function TokenRow({ token }: { token: ApiToken }) {
  const revoke = useRevokeToken()
  const [confirming, setConfirming] = useState(false)
  const revoked = token.revoked_at !== null

  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-sm">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-bold text-foreground">{token.name}</span>
          {revoked && (
            <Badge variant="outline" className="font-normal">
              revoked
            </Badge>
          )}
        </div>
        <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-ex-neutral-400">
          <code className="font-mono">{token.prefix}…</code>
          <span title={absoluteTime(token.created_at)}>created {relativeTime(token.created_at)}</span>
          <span title={absoluteTime(token.last_used_at)}>
            {token.last_used_at ? `last used ${relativeTime(token.last_used_at)}` : 'never used'}
          </span>
        </div>
      </div>

      {!revoked &&
        (confirming ? (
          <span className="flex items-center gap-2">
            <span className="text-xs text-ex-neutral-500">Revoke it?</span>
            <Button
              variant="destructive"
              size="sm"
              disabled={revoke.isPending}
              onClick={() => revoke.mutate(token.id)}
            >
              <Check className="h-3.5 w-3.5" />
              Yes
            </Button>
            <Button variant="outline" size="sm" onClick={() => setConfirming(false)}>
              <X className="h-3.5 w-3.5" />
              No
            </Button>
          </span>
        ) : (
          <Button variant="outline" size="sm" onClick={() => setConfirming(true)}>
            Revoke
          </Button>
        ))}
    </li>
  )
}

/* ------------------------------------------------------------- display name */

function DisplayNameEditor({ current }: { current: string }) {
  const update = useUpdateDisplayName()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(current)

  if (!editing) {
    return (
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          setValue(current)
          setEditing(true)
        }}
      >
        <Pencil className="h-3.5 w-3.5" />
        Edit name
      </Button>
    )
  }

  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        update.mutate(value.trim() || null, { onSuccess: () => setEditing(false) })
      }}
    >
      <Input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        aria-label="Display name"
        autoFocus
        className="h-9 w-56"
      />
      <Button type="submit" variant="primary" size="sm" disabled={update.isPending}>
        Save
      </Button>
      <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(false)}>
        Cancel
      </Button>
    </form>
  )
}

/* -------------------------------------------------------------------- page */

export function MePage() {
  const overview = useMyOverview()
  const handle = overview.data?.profile.handle
  // The profile endpoint is what carries a handle's bundles; /me is identity
  // plus tokens only, so My hub reads both.
  const mine = useProfile(handle)

  const [minting, setMinting] = useState(false)
  const [creating, setCreating] = useState(false)

  if (overview.isLoading) {
    return (
      <div className="mx-auto flex max-w-6xl flex-col gap-6 p-8">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }

  if (overview.isError || !overview.data) {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <EmptyState
          icon={KeyRound}
          title="Could not load your hub"
          description={
            overview.error instanceof Error ? overview.error.message : 'The hub API did not respond.'
          }
          action={
            <Button variant="outline" size="sm" onClick={() => void overview.refetch()}>
              Retry
            </Button>
          }
        />
      </div>
    )
  }

  const { profile, tokens } = overview.data
  const bundles = mine.data?.bundles ?? []

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-8 p-8">
      <ProfileHeader profile={profile}>
        <DisplayNameEditor current={profile.display_name} />
      </ProfileHeader>

      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-sm font-bold text-foreground">My bundles</h2>
          <Button variant="outline" size="sm" onClick={() => setCreating(true)}>
            <Plus className="h-3.5 w-3.5" />
            New bundle
          </Button>
        </div>

        {mine.isLoading ? (
          <BundleGridSkeleton count={3} />
        ) : bundles.length === 0 ? (
          <EmptyState
            icon={Package}
            title="You have not published anything"
            description="Create a bundle here, then push content into it from phi with /hub push."
            action={
              <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
                <Plus className="h-4 w-4" />
                New bundle
              </Button>
            }
          />
        ) : (
          <BundleGrid bundles={bundles} />
        )}
      </section>

      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-sm font-bold text-foreground">API tokens</h2>
            <p className="text-xs text-muted-foreground">
              Bearer credentials for phi and for anything else that talks to the hub API.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => setMinting(true)}>
            <KeyRound className="h-3.5 w-3.5" />
            New token
          </Button>
        </div>

        {tokens.length === 0 ? (
          <EmptyState
            icon={KeyRound}
            title="No tokens yet"
            description="Run /hub login in phi and approve it on the Connect page — that is the normal path, and the token never touches your clipboard."
          />
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {tokens.map((token) => (
              <TokenRow key={token.id} token={token} />
            ))}
          </ul>
        )}
      </section>

      {minting && <MintTokenDialog onClose={() => setMinting(false)} />}
      {creating && <NewBundleDialog onClose={() => setCreating(false)} />}
    </div>
  )
}
