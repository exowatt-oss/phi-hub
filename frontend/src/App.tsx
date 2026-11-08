import { LayoutGrid, LogOut, MonitorSmartphone, Store, User as UserIcon } from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'

import { logout } from '@/api/hooks'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/auth'
import { cn, initials } from '@/lib/utils'

const NAV = [
  { to: '/', label: 'Marketplace', icon: Store, end: true },
  { to: '/browse', label: 'All bundles', icon: LayoutGrid, end: false },
  { to: '/me', label: 'My hub', icon: UserIcon, end: false },
  { to: '/connect', label: 'Connect a device', icon: MonitorSmartphone, end: false },
]

/**
 * Shell: a thin neutral sidebar plus the routed outlet. Data-app chrome per the
 * the data-app design — the gradient appears exactly twice on this surface (the
 * wordmark and the user avatar), never as a background.
 */
export default function App() {
  const { user } = useAuth()

  async function handleLogout() {
    // logout() resolves the platform sign-out URL and hard-navigates there
    // via window.location, so there's no in-app route to push afterwards.
    // the configured identity provider signs the user out and redirects back
    // to this app's host (where RequireAuth re-runs).
    await logout()
  }

  return (
    <div className="flex min-h-screen bg-ex-neutral-100">
      <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-background p-4">
        <div className="mb-6">
          <h1 className="ex-text-brand text-xl font-bold tracking-tight">phi hub</h1>
          <p className="text-xs text-muted-foreground">Agent config registry</p>
        </div>

        <nav className="flex flex-1 flex-col gap-1 text-sm">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2 rounded-md px-2.5 py-2 transition-colors',
                  isActive
                    ? 'bg-secondary font-bold text-foreground'
                    : 'text-ex-neutral-500 hover:bg-secondary hover:text-foreground',
                )
              }
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              {label}
            </NavLink>
          ))}
        </nav>

        {user && (
          <div className="mt-4 flex items-center justify-between gap-2 border-t border-border pt-4 text-sm">
            <div className="flex min-w-0 items-center gap-2">
              <div className="ex-bg-gradient flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold">
                {initials(user.display_name ?? user.name ?? user.email)}
              </div>
              <div className="min-w-0">
                <div className="truncate font-bold">
                  {user.display_name ?? user.name ?? user.handle}
                </div>
                <div className="truncate font-mono text-xs text-muted-foreground">
                  {user.handle}
                </div>
              </div>
            </div>
            <Button variant="ghost" size="icon" onClick={handleLogout} title="Sign out">
              <LogOut className="h-4 w-4" />
              <span className="sr-only">Sign out</span>
            </Button>
          </div>
        )}
      </aside>

      <main className="min-w-0 flex-1">
        <Outlet />
      </main>
    </div>
  )
}
