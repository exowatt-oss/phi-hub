import { useState, type FormEvent, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { useCreateBundle, useOrgs, useUpdateBundle } from '@/api/hooks'
import { BUNDLE_KINDS, type BundleKind, type BundleSummary } from '@/api/types'
import { useAuth } from '@/lib/auth'
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
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

/*
 * Both dialogs are mounted only while they are open (callers render them
 * conditionally). That is what lets every field below be plain `useState` with
 * an initializer: each open is a fresh mount, so there is no stale draft and
 * no reset-on-open effect that could fight a background refetch.
 */

/** Server-side rule: `[a-z0-9][a-z0-9-]{1,63}` (README.md). */
function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64)
}

function parseTags(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[,\s]+/)
        .map((t) => t.trim().toLowerCase())
        .filter(Boolean),
    ),
  )
}

/** The kind picker. A native select keeps focus and keyboard behaviour free
 *  and avoids pulling in a Radix primitive the app doesn't otherwise use. */
function KindSelect({
  value,
  onChange,
  id,
}: {
  value: BundleKind
  onChange: (kind: BundleKind) => void
  id: string
}) {
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value as BundleKind)}
      className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
    >
      {BUNDLE_KINDS.map((kind) => (
        <option key={kind} value={kind}>
          {kind}
        </option>
      ))}
    </select>
  )
}

/** Soft-red notice. The brand red is too saturated to sit inside body copy,
 *  so the toast palette from the branding rules is used verbatim. */
export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-[#f5c2bc] bg-[#fff5f4] px-3 py-2 text-sm text-[#a51a14]"
    >
      {children}
    </div>
  )
}

/* ------------------------------------------------------------------ create */

export function NewBundleDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const create = useCreateBundle()
  const { user } = useAuth()
  const orgs = useOrgs()

  const [title, setTitle] = useState('')
  const [slug, setSlug] = useState('')
  const [slugTouched, setSlugTouched] = useState(false)
  const [description, setDescription] = useState('')
  const [kind, setKind] = useState<BundleKind>('skill')
  const [tags, setTags] = useState('')
  // Empty string = publish as yourself. Only orgs the server says you
  // publish for are offered, so the picker cannot produce a 403.
  const [owner, setOwner] = useState('')

  const publishableOrgs = (orgs.data ?? []).filter((o) => o.is_publisher)

  // The slug tracks the title until the user takes it over — most bundles want
  // the obvious slug, and the ones that don't are edited before the first save.
  const effectiveSlug = slugTouched ? slug : slugify(title)
  const error = create.error instanceof ApiError ? create.error : null
  // 409 = someone already owns that slug; 422 = the slug failed validation.
  const slugRejected = error !== null && (error.status === 409 || error.status === 422)
  const ownerHandle = owner || user?.handle || 'you'

  function submit(event: FormEvent) {
    event.preventDefault()
    create.mutate(
      {
        slug: effectiveSlug,
        title: title.trim() || effectiveSlug,
        description: description.trim(),
        kind,
        tags: parseTags(tags),
        ...(owner ? { owner } : {}),
      },
      {
        onSuccess: (bundle: BundleSummary) => {
          onClose()
          navigate(`/b/${bundle.owner}/${bundle.slug}`)
        },
      },
    )
  }

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New bundle</DialogTitle>
          <DialogDescription>
            Creates the empty registry entry. Push content into it from phi with{' '}
            <code className="font-mono">/hub push</code>.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="flex flex-col gap-4">
          {/* Only rendered when you actually publish for an org — for
              everyone else there is no choice to make. */}
          {publishableOrgs.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="bundle-owner">Publish as</Label>
              <select
                id="bundle-owner"
                value={owner}
                onChange={(e) => setOwner(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                <option value="">{user?.handle ?? 'me'} (personal)</option>
                {publishableOrgs.map((org) => (
                  <option key={org.profile.handle} value={org.profile.handle}>
                    {org.profile.handle} (org — marketplace)
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                An org bundle appears on the Marketplace and can be edited by any of that org&apos;s
                publishers. Your name still goes on every version you publish.
              </p>
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="bundle-title">Title</Label>
            <Input
              id="bundle-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Routing setup for site-controller work"
              autoFocus
              required
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="bundle-slug">Slug</Label>
            <Input
              id="bundle-slug"
              value={effectiveSlug}
              onChange={(e) => {
                setSlugTouched(true)
                setSlug(e.target.value)
              }}
              placeholder="routing-setup"
              className={cn('font-mono', slugRejected && 'border-destructive')}
              required
            />
            <p className="text-xs text-muted-foreground">
              Published as{' '}
              <code className="font-mono">
                {ownerHandle}/{effectiveSlug || 'slug'}
              </code>
              . Lowercase letters, digits and hyphens, and permanent — every{' '}
              <code className="font-mono">owner/slug</code> reference sitting in someone else&apos;s
              phi depends on it.
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="bundle-description">Description</Label>
            <Textarea
              id="bundle-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What it does, and who should take it."
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="bundle-kind">Kind</Label>
              <KindSelect id="bundle-kind" value={kind} onChange={setKind} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="bundle-tags">Tags</Label>
              <Input
                id="bundle-tags"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="ems, review"
              />
            </div>
          </div>

          {error && <ErrorNote>{error.message}</ErrorNote>}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            {/* Server contract: slugs are min length 2 (BundleCreateIn). */}
            <Button type="submit" variant="primary" disabled={create.isPending || effectiveSlug.length < 2}>
              {create.isPending ? 'Creating…' : 'Create bundle'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/* -------------------------------------------------------------------- edit */

export function EditBundleDialog({
  bundle,
  onClose,
}: {
  bundle: BundleSummary
  onClose: () => void
}) {
  const update = useUpdateBundle(bundle.owner, bundle.slug)

  const [title, setTitle] = useState(bundle.title)
  const [description, setDescription] = useState(bundle.description)
  const [kind, setKind] = useState<BundleKind>(bundle.kind as BundleKind)
  const [tags, setTags] = useState(bundle.tags.join(', '))

  const error = update.error instanceof ApiError ? update.error : null

  function submit(event: FormEvent) {
    event.preventDefault()
    update.mutate(
      {
        title: title.trim(),
        description: description.trim(),
        kind,
        tags: parseTags(tags),
      },
      { onSuccess: () => onClose() },
    )
  }

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit {bundle.ref}</DialogTitle>
          <DialogDescription>
            Metadata only. The slug and the published files are immutable from here.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-title">Title</Label>
            <Input id="edit-title" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-description">Description</Label>
            <Textarea
              id="edit-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="edit-kind">Kind</Label>
              <KindSelect id="edit-kind" value={kind} onChange={setKind} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="edit-tags">Tags</Label>
              <Input id="edit-tags" value={tags} onChange={(e) => setTags(e.target.value)} />
            </div>
          </div>

          {error && <ErrorNote>{error.message}</ErrorNote>}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={update.isPending}>
              {update.isPending ? 'Saving…' : 'Save changes'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
