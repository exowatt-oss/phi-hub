import { CheckCircle2, KeyRound, MonitorSmartphone, ShieldQuestion, XCircle } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { useDeviceDecision, useDevicePending } from '@/api/hooks'
import { ErrorNote } from '@/components/BundleForm'
import { EmptyState } from '@/components/EmptyState'
import { absoluteTime, relativeTime } from '@/components/format'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'

/** User codes are 8 chars of A-Z2-7 — the CLI prints them uppercase. */
function normalizeCode(raw: string): string {
  return raw.toUpperCase().replace(/[^A-Z2-7]/g, '').slice(0, 8)
}

function Shell({ children }: { children: ReactNode }) {
  return <div className="mx-auto flex max-w-xl flex-col gap-6 p-8">{children}</div>
}

/** The manual entry form, shown when the URL carries no `?code=`. */
function CodeEntry({ onSubmit }: { onSubmit: (code: string) => void }) {
  const [value, setValue] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    if (value.length > 0) onSubmit(value)
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-4 p-6">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">Connect a device</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Running <code className="font-mono">/hub login</code> in phi prints an eight-character
            code. Type it here to approve that terminal.
          </p>
        </div>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="device-code">Device code</Label>
            <Input
              id="device-code"
              value={value}
              // Uppercased as the user types: the codes are printed uppercase
              // and the backend normalizes anyway, so echoing lowercase would
              // just look like a mismatch.
              onChange={(e) => setValue(normalizeCode(e.target.value))}
              placeholder="K3M7QP2Z"
              autoFocus
              autoComplete="off"
              spellCheck={false}
              className="font-mono text-lg tracking-[0.3em]"
            />
          </div>
          <Button type="submit" variant="primary" disabled={value.length === 0}>
            Look up code
          </Button>
        </form>
      </CardContent>
    </Card>
  )
}

export function ConnectPage() {
  const [params, setParams] = useSearchParams()
  const code = normalizeCode(params.get('code') ?? '') || null

  const pending = useDevicePending(code)
  const decision = useDeviceDecision(code)

  // Track the outcome locally: after a decision the query refetches, but the
  // success copy should key off what THIS user just did, not off a status the
  // backend might also reach some other way.
  const [decided, setDecided] = useState<'approve' | 'deny' | null>(null)

  if (!code) {
    return (
      <Shell>
        <CodeEntry onSubmit={(next) => setParams({ code: next })} />
      </Shell>
    )
  }

  if (pending.isLoading) {
    return (
      <Shell>
        <Skeleton className="h-56 w-full" />
      </Shell>
    )
  }

  if (pending.isError) {
    const notFound = pending.error instanceof ApiError && pending.error.status === 404
    return (
      <Shell>
        <EmptyState
          icon={ShieldQuestion}
          title={notFound ? 'That code does not match a request' : 'Could not check that code'}
          description={
            notFound
              ? 'Device codes live for fifteen minutes. Run /hub login again in your terminal to get a fresh one.'
              : (pending.error as Error).message
          }
          action={
            <Button variant="outline" size="sm" onClick={() => setParams({})}>
              Enter a different code
            </Button>
          }
        />
      </Shell>
    )
  }

  if (!pending.data) return null
  const device = pending.data
  const status = decided === 'approve' ? 'approved' : decided === 'deny' ? 'denied' : device.status

  if (status === 'approved') {
    return (
      <Shell>
        <Card className="ex-stripe-top">
          <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
            {/* Neutral, not green: the palette has one accent (the gradient),
                which the card's top stripe already spends. */}
            <CheckCircle2 className="h-7 w-7 text-ex-neutral-700" aria-hidden="true" />
            <h1 className="text-xl font-bold tracking-tight text-foreground">Device approved</h1>
            <p className="text-sm text-muted-foreground">
              Return to your terminal — <strong className="font-bold">{device.device_name}</strong>{' '}
              picks up its token on the next poll, usually within a few seconds. The token is never
              shown in this browser.
            </p>
          </CardContent>
        </Card>
      </Shell>
    )
  }

  if (status === 'denied' || status === 'expired') {
    return (
      <Shell>
        <EmptyState
          icon={XCircle}
          title={status === 'denied' ? 'Request denied' : 'Request expired'}
          description={
            status === 'denied'
              ? `${device.device_name} was refused and got no token. Denial is final — a new /hub login starts a fresh request.`
              : `${device.device_name} waited too long. Run /hub login again to start over.`
          }
          action={
            <Button variant="outline" size="sm" onClick={() => setParams({})}>
              Enter a different code
            </Button>
          }
        />
      </Shell>
    )
  }

  return (
    <Shell>
      <Card className="ex-stripe-top">
        <CardContent className="flex flex-col gap-5 p-6">
          <div className="flex items-start gap-3">
            <MonitorSmartphone className="mt-0.5 h-5 w-5 shrink-0 text-ex-neutral-400" aria-hidden="true" />
            <div className="min-w-0">
              <h1 className="text-xl font-bold tracking-tight text-foreground">
                Approve {device.device_name}?
              </h1>
              <p className="mt-1 text-sm text-muted-foreground">
                A phi installation is asking to connect to the hub as you.
              </p>
            </div>
          </div>

          <dl className="grid grid-cols-[8rem_1fr] gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-ex-neutral-400">Device</dt>
            <dd className="truncate font-bold text-foreground">{device.device_name}</dd>
            <dt className="text-ex-neutral-400">Code</dt>
            <dd className="font-mono tracking-widest text-foreground">{device.user_code}</dd>
            <dt className="text-ex-neutral-400">Requested</dt>
            <dd className="text-ex-neutral-700" title={absoluteTime(device.requested_at)}>
              {relativeTime(device.requested_at)}
            </dd>
            <dt className="text-ex-neutral-400">Expires</dt>
            <dd className="text-ex-neutral-700" title={absoluteTime(device.expires_at)}>
              {relativeTime(device.expires_at)}
            </dd>
          </dl>

          <div className="flex items-start gap-2 rounded-lg border border-border bg-ex-neutral-100 p-3 text-xs text-ex-neutral-500">
            <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <p>
              Approving mints a token that can read every bundle in the hub and publish under your
              handle. Only approve a request you started yourself, on a machine you control. You can
              revoke the token any time from My hub.
            </p>
          </div>

          {decision.isError && <ErrorNote>{(decision.error as Error).message}</ErrorNote>}

          <div className="flex gap-2">
            <Button
              variant="primary"
              disabled={decision.isPending}
              onClick={() =>
                decision.mutate('approve', { onSuccess: () => setDecided('approve') })
              }
            >
              Approve
            </Button>
            <Button
              variant="outline"
              disabled={decision.isPending}
              onClick={() => decision.mutate('deny', { onSuccess: () => setDecided('deny') })}
            >
              Deny
            </Button>
          </div>
        </CardContent>
      </Card>
    </Shell>
  )
}
