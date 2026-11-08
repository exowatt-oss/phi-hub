import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'

import { cn } from '@/lib/utils'

/**
 * The Marketplace / All bundles pair.
 *
 * Real routes rather than local tab state, so a link to the curated view
 * and a link to the firehose are two different URLs someone can paste into
 * Slack. Styled as tabs because that is the relationship: two views of one
 * registry, not two features.
 */
const TABS = [
  { to: '/', label: 'Marketplace', end: true },
  { to: '/browse', label: 'All bundles', end: false },
]

export function BrowseTabs() {
  return (
    <div
      className="inline-flex items-center gap-1 rounded-md bg-secondary p-1"
      role="tablist"
      aria-label="Bundle views"
    >
      {TABS.map(({ to, label, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          role="tab"
          className={({ isActive }) =>
            cn(
              'rounded-sm px-3 py-1.5 text-sm transition-colors',
              isActive
                ? 'bg-background font-bold text-foreground shadow-sm'
                : 'text-ex-neutral-500 hover:text-foreground',
            )
          }
        >
          {label}
        </NavLink>
      ))}
    </div>
  )
}

/** Pill used by the kind filters and the sort toggle on both views. */
export function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'rounded-full border px-3 py-1 text-xs transition-colors',
        active
          ? 'border-transparent bg-ex-neutral-900 text-ex-neutral-0'
          : 'border-border bg-background text-ex-neutral-500 hover:border-ex-neutral-300 hover:text-foreground',
      )}
    >
      {children}
    </button>
  )
}
