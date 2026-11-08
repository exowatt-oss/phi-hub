import {
  Archive,
  Download,
  FileQuestion,
  FileWarning,
  Pencil,
  Trash2,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { useBundle, useDeleteBundle } from '@/api/hooks'
import { EditBundleDialog, ErrorNote } from '@/components/BundleForm'
import { EmptyState } from '@/components/EmptyState'
import { FileTree } from '@/components/FileTree'
import { absoluteTime, relativeTime } from '@/components/format'
import { ImportDialog } from '@/components/ImportDialog'
import { KindBadge } from '@/components/KindBadge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'

/** Section wrapper — a labelled block in the detail page's single column. */
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm font-bold text-foreground">{title}</h2>
      {children}
    </section>
  )
}

export function BundleDetailPage() {
  const { owner, slug } = useParams<{ owner: string; slug: string }>()
  const navigate = useNavigate()

  const detail = useBundle(owner, slug)
  const remove = useDeleteBundle()

  const [importing, setImporting] = useState(false)
  const [editing, setEditing] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  if (detail.isLoading) {
    return (
      <div className="mx-auto flex max-w-4xl flex-col gap-6 p-8">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }

  // A 404 is an ordinary outcome here (stale link, deleted bundle, typo in a
  // hand-written ref), so it gets a card rather than the router error boundary.
  if (detail.isError) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404
    return (
      <div className="mx-auto max-w-2xl p-8">
        <EmptyState
          icon={notFound ? FileQuestion : FileWarning}
          title={notFound ? 'No such bundle' : 'Could not load this bundle'}
          description={
            notFound ? (
              <>
                <code className="font-mono">
                  {owner}/{slug}
                </code>{' '}
                is not in the registry. It may have been deleted, or the reference may be
                misspelled.
              </>
            ) : (
              (detail.error as Error).message
            )
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

  if (!detail.data) return null

  // `can_write`, not `is_owner`: for an ORG-owned bundle every publisher on
  // the org's roster may edit and delete, and the server has already
  // answered that question.
  const { bundle, owner_profile, versions, files, recent_events, can_write } = detail.data
  const sortedVersions = [...versions].sort((a, b) => b.version - a.version)
  const archiveHref = `/api/bundles/${bundle.owner}/${bundle.slug}/versions/latest/archive.tgz`

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-8 p-8">
      <header className="ex-stripe-top flex flex-col gap-4 rounded-lg border border-border bg-card p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-bold tracking-tight text-foreground">
                {bundle.title || bundle.slug}
              </h1>
              <KindBadge kind={bundle.kind} />
            </div>
            <p className="mt-1 font-mono text-sm text-muted-foreground">
              <Link to={`/p/${bundle.owner}`} className="hover:text-foreground hover:underline">
                {bundle.owner}
              </Link>
              /{bundle.slug}
            </p>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            {can_write && (
              <>
                <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                  <Pencil className="h-3.5 w-3.5" />
                  Edit
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setConfirmingDelete(true)}
                  className="text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Delete
                </Button>
              </>
            )}
            <Button variant="primary" onClick={() => setImporting(true)}>
              <Download className="h-4 w-4" />
              Import
            </Button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-ex-neutral-500">
          <span>
            <strong className="font-bold text-foreground">{bundle.install_count}</strong> installs
          </span>
          <span>
            {bundle.latest_version > 0 ? (
              <>
                latest <strong className="font-bold text-foreground">v{bundle.latest_version}</strong>
              </>
            ) : (
              'no published version'
            )}
          </span>
          <span title={absoluteTime(bundle.updated_at)}>
            updated {relativeTime(bundle.updated_at)}
          </span>
          <span>
            by{' '}
            <Link to={`/p/${bundle.owner}`} className="hover:text-foreground hover:underline">
              {owner_profile.display_name || owner_profile.handle}
            </Link>
          </span>
          {bundle.latest_version > 0 && (
            <a
              href={archiveHref}
              className="ml-auto inline-flex items-center gap-1.5 hover:text-foreground hover:underline"
            >
              <Archive className="h-3.5 w-3.5" aria-hidden="true" />
              archive.tgz
            </a>
          )}
        </div>

        {bundle.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {bundle.tags.map((tag) => (
              <Badge key={tag} variant="outline" className="font-normal">
                {tag}
              </Badge>
            ))}
          </div>
        )}
      </header>

      {confirmingDelete && (
        <ErrorNote>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>
              Delete <code className="font-mono">{bundle.ref}</code>? Anyone who has already
              imported it keeps their local copy, but the reference stops resolving.
            </span>
            <span className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setConfirmingDelete(false)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                size="sm"
                disabled={remove.isPending}
                onClick={() =>
                  remove.mutate(
                    { owner: bundle.owner, slug: bundle.slug },
                    { onSuccess: () => navigate('/') },
                  )
                }
              >
                {remove.isPending ? 'Deleting…' : 'Delete for good'}
              </Button>
            </span>
          </div>
          {remove.isError && (
            <p className="mt-2">{(remove.error as Error).message}</p>
          )}
        </ErrorNote>
      )}

      {bundle.description && (
        <Section title="About">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-ex-neutral-700">
            {bundle.description}
          </p>
        </Section>
      )}

      <Section title={`Files${files.length > 0 ? ` (${files.length})` : ''}`}>
        {files.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
            Nothing published yet. The owner pushes content with{' '}
            <code className="font-mono">/hub push</code>.
          </p>
        ) : (
          <FileTree owner={bundle.owner} slug={bundle.slug} files={files} />
        )}
      </Section>

      <Section title="Versions">
        {sortedVersions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No versions published.</p>
        ) : (
          <ol className="divide-y divide-border rounded-lg border border-border">
            {sortedVersions.map((version) => (
              <li key={version.version} className="flex gap-4 px-4 py-3">
                <span className="w-10 shrink-0 font-mono text-sm font-bold text-foreground">
                  v{version.version}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="whitespace-pre-wrap text-sm text-ex-neutral-700">
                    {version.changelog || 'No changelog.'}
                  </p>
                  <p className="mt-1 text-xs text-ex-neutral-400">
                    {version.created_by ? (
                      <Link
                        to={`/p/${version.created_by}`}
                        className="hover:text-foreground hover:underline"
                      >
                        {version.created_by}
                      </Link>
                    ) : (
                      'unknown'
                    )}
                    {' · '}
                    <span title={absoluteTime(version.created_at)}>
                      {relativeTime(version.created_at)}
                    </span>
                    {' · '}
                    {version.file_count} file{version.file_count === 1 ? '' : 's'}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        )}
      </Section>

      <Section title="Recent activity">
        {recent_events.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nobody has imported this yet.</p>
        ) : (
          <ul className="flex flex-col gap-1.5 text-xs text-ex-neutral-500">
            {recent_events.map((event, i) => (
              <li key={`${event.handle}-${event.created_at}-${i}`} className="flex gap-2">
                <Link
                  to={`/p/${event.handle}`}
                  className="font-bold text-foreground hover:underline"
                >
                  {event.handle}
                </Link>
                <span>{event.action}</span>
                {event.version !== null && <span className="font-mono">v{event.version}</span>}
                <span className="ml-auto" title={absoluteTime(event.created_at)}>
                  {relativeTime(event.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* Both dialogs mount only while open — see the note in BundleForm.tsx. */}
      {importing && <ImportDialog detail={detail.data} onClose={() => setImporting(false)} />}
      {editing && <EditBundleDialog bundle={bundle} onClose={() => setEditing(false)} />}
    </div>
  )
}
