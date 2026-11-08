import { FileText, FolderClosed, Lock } from 'lucide-react'
import { useMemo, useState } from 'react'

import { useVersionFiles } from '@/api/hooks'
import type { BundleFileEntry } from '@/api/types'
import { decodeBase64Utf8, formatBytes, splitPath } from '@/components/format'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'

interface FileTreeProps {
  owner: string
  slug: string
  files: BundleFileEntry[]
}

/**
 * The latest version's file listing, grouped by directory, with a lazy viewer
 * for text files.
 *
 * Contents are a separate (and much larger) request than the listing, so they
 * are only fetched once someone actually opens a file — `everOpened` latches
 * true so closing the viewer doesn't disable the query and throw the cache
 * away mid-browse.
 */
export function FileTree({ owner, slug, files }: FileTreeProps) {
  const [selected, setSelected] = useState<BundleFileEntry | null>(null)
  const [everOpened, setEverOpened] = useState(false)

  const contents = useVersionFiles(owner, slug, 'latest', everOpened)

  const groups = useMemo(() => {
    const byDir = new Map<string, BundleFileEntry[]>()
    for (const file of files) {
      const { dir } = splitPath(file.path)
      const bucket = byDir.get(dir)
      if (bucket) bucket.push(file)
      else byDir.set(dir, [file])
    }
    // Root files first, then directories alphabetically — the root is where a
    // pi package keeps package.json, so it is the natural first thing to read.
    return [...byDir.entries()]
      .sort(([a], [b]) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b)))
      .map(([dir, entries]) => ({
        dir,
        entries: [...entries].sort((a, b) => a.path.localeCompare(b.path)),
      }))
  }, [files])

  function open(file: BundleFileEntry) {
    if (!file.is_text) return
    setEverOpened(true)
    setSelected(file)
  }

  const body = selected
    ? contents.data?.files.find((f) => f.path === selected.path)?.content_b64
    : undefined

  return (
    <>
      <div className="divide-y divide-border rounded-lg border border-border">
        {groups.map(({ dir, entries }) => (
          <div key={dir || '.'}>
            <div className="flex items-center gap-1.5 bg-ex-neutral-100 px-3 py-1.5 font-mono text-xs text-ex-neutral-500">
              <FolderClosed className="h-3 w-3" aria-hidden="true" />
              {dir === '' ? '(root)' : `${dir}/`}
            </div>
            <ul>
              {entries.map((file) => {
                const { name } = splitPath(file.path)
                return (
                  <li key={file.path}>
                    <button
                      type="button"
                      onClick={() => open(file)}
                      disabled={!file.is_text}
                      title={file.is_text ? `View ${file.path}` : 'Binary file'}
                      className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors enabled:hover:bg-ex-neutral-100 disabled:cursor-default"
                    >
                      {file.is_text ? (
                        <FileText className="h-3.5 w-3.5 shrink-0 text-ex-neutral-400" aria-hidden="true" />
                      ) : (
                        <Lock className="h-3.5 w-3.5 shrink-0 text-ex-neutral-300" aria-hidden="true" />
                      )}
                      <span
                        className={
                          file.is_text
                            ? 'truncate font-mono text-foreground'
                            : 'truncate font-mono text-ex-neutral-400'
                        }
                      >
                        {name}
                      </span>
                      {!file.is_text && (
                        <span className="shrink-0 text-[11px] text-ex-neutral-400">
                          binary — download the archive
                        </span>
                      )}
                      <span className="ml-auto shrink-0 tabular-nums text-ex-neutral-400">
                        {formatBytes(file.size)}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </div>

      {selected && (
        <Dialog open onOpenChange={(next) => !next && setSelected(null)}>
          <DialogContent className="max-w-3xl">
            <DialogHeader>
              <DialogTitle className="truncate font-mono text-sm">{selected.path}</DialogTitle>
              <DialogDescription>
                {formatBytes(selected.size)} · latest version
              </DialogDescription>
            </DialogHeader>

            {contents.isLoading && (
              <div className="flex flex-col gap-2">
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-11/12" />
                <Skeleton className="h-3 w-3/4" />
              </div>
            )}

            {contents.isError && (
              <p className="text-sm text-muted-foreground">
                Could not load this file&apos;s contents. Download the archive instead.
              </p>
            )}

            {body !== undefined && (
              <pre className="max-h-[60vh] overflow-auto rounded-lg border border-border bg-ex-neutral-100 p-3 font-mono text-xs leading-relaxed text-ex-neutral-700">
                {decodeBase64Utf8(body)}
              </pre>
            )}

            {!contents.isLoading && !contents.isError && body === undefined && (
              <p className="text-sm text-muted-foreground">
                That file is not in the latest version any more.
              </p>
            )}
          </DialogContent>
        </Dialog>
      )}
    </>
  )
}
