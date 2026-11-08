import { useRef } from 'react'

import { useRecordImport } from '@/api/hooks'
import type { BundleDetail } from '@/api/types'
import { CopyBlock } from '@/components/CopyBlock'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

/**
 * The ways to take a bundle: install (tracking latest), pin to this exact
 * version, a prompt for any MCP-connected agent, and raw curl.
 *
 * Install is first and framed as the default because tracking latest is
 * what makes the marketplace work — a pinned bundle silently stops
 * receiving the publisher's fixes. Pinning is offered right underneath so
 * the choice is visible rather than buried in docs.
 *
 * Mounted only while open (the caller conditionally renders it), so per-open
 * state is just component state — no reset-on-open effects to keep in sync.
 */
export function ImportDialog({
  detail,
  onClose,
}: {
  detail: BundleDetail
  onClose: () => void
}) {
  const { bundle, import_snippets } = detail
  const record = useRecordImport(bundle.owner, bundle.slug)

  // One import event per time the modal is opened, not per copy click. A user
  // who copies the command, mistypes it and copies again has imported the
  // bundle once — counting both would inflate every install count on the site.
  const recorded = useRef(false)

  function handlePhiCopied() {
    if (recorded.current) return
    recorded.current = true
    record.mutate({
      action: 'imported',
      version: bundle.latest_version > 0 ? bundle.latest_version : undefined,
    })
  }

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Import {bundle.ref}</DialogTitle>
          <DialogDescription>
            An import lands in phi&apos;s isolated bundle store and links in as a single{' '}
            <code className="rounded bg-ex-neutral-100 px-1 font-mono">packages</code> entry — your
            own skills, extensions and settings are never touched, and removing it is one
            reversible step.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <CopyBlock
            label="Install in phi"
            hint="Paste this at the phi prompt. It tracks the latest version — /hub update and phi's startup check pull new versions as the publisher ships them."
            value={import_snippets.phi_install || import_snippets.phi_command}
            onCopied={handlePhiCopied}
          />
          {bundle.latest_version > 0 && import_snippets.phi_pin && (
            <CopyBlock
              label={`Pin to v${bundle.latest_version}`}
              hint="Same install, frozen at this version. Nothing will move it again — including the publisher's fixes."
              value={import_snippets.phi_pin}
              onCopied={handlePhiCopied}
            />
          )}
          <CopyBlock
            label="Any MCP-connected agent"
            hint="No phi CLI, but an agent that can reach the hub's MCP surface. Hand it this prompt."
            value={import_snippets.agent_prompt}
          />
          <CopyBlock
            label="Raw API"
            hint="Scripting it yourself. Returns every file base64-encoded; needs a phk_ token in $PHI_HUB_TOKEN."
            value={import_snippets.curl}
          />
        </div>

        {bundle.latest_version === 0 && (
          <p className="text-xs text-muted-foreground">
            This bundle has no published version yet, so the snippets above will not resolve until
            its owner runs <code className="font-mono">/hub push</code>.
          </p>
        )}
      </DialogContent>
    </Dialog>
  )
}
