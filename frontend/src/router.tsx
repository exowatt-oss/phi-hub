import { useEffect, type ReactNode } from 'react'
import { Navigate, Outlet, createBrowserRouter } from 'react-router-dom'

import App from '@/App'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/lib/auth'

import { BundleDetailPage } from '@/pages/BundleDetail'
import { ConnectPage } from '@/pages/Connect'
import { HomePage } from '@/pages/Home'
import { MarketplacePage } from '@/pages/Marketplace'
import { MePage } from '@/pages/Me'
import { ProfilePage } from '@/pages/Profile'

// One-shot guard so a failed re-auth cannot loop through a configured IdP.
const SSO_RETRY_KEY = 'app_sso_retry'

/** Optional external sign-in entry point supplied by the host at build time. */
function signInUrl(): string | undefined {
  const base = import.meta.env.VITE_SIGN_IN_URL?.trim()
  if (!base) return undefined
  const url = new URL(base)
  url.searchParams.set('rd', window.location.href)
  return url.toString()
}

function FullScreen({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-ex-neutral-100 px-4">
      {children}
    </div>
  )
}

/** An unauthenticated request never sees bundles. Redirect once only when
 * an operator configured an external login URL; otherwise show instructions.
 * Network/500 errors remain on a manual retry card, not an IdP redirect.
 */
export function RequireAuth() {
  const { user, isLoading, isError, error, refetch } = useAuth()
  // Only treat "no user" as "needs login" when the /api/auth/me call
  // completed cleanly. A backend outage / network error surfaces as
  // `isError` from TanStack Query and is handled separately — auto-
  // bouncing through the IdP wouldn't fix it (and could strand the user
  // on the retry card or loop them through the identity provider).
  const needsLogin = !isLoading && !isError && !user
  const alreadyRetried = typeof window !== 'undefined' && !!sessionStorage.getItem(SSO_RETRY_KEY)
  const loginUrl = signInUrl()

  // If configured, bounce through the IdP at most once.
  useEffect(() => {
    if (user) {
      sessionStorage.removeItem(SSO_RETRY_KEY)
      return
    }
    if (needsLogin && !alreadyRetried && loginUrl) {
      sessionStorage.setItem(SSO_RETRY_KEY, '1')
      window.location.replace(loginUrl)
    }
  }, [user, needsLogin, alreadyRetried, loginUrl])

  if (isLoading || (needsLogin && !alreadyRetried && loginUrl)) {
    return (
      <FullScreen>
        <Skeleton className="h-24 w-64" />
      </FullScreen>
    )
  }

  if (isError) {
    // The backend is unreachable / returning non-401 errors. NOT an
    // SSO failure — bouncing through the IdP would just come back to the
    // same broken state. Offer a manual retry against /api/auth/me.
    const message = error instanceof Error ? error.message : 'Unknown error'
    return (
      <FullScreen>
        <Card className="w-full max-w-md">
          <CardContent className="flex flex-col items-center gap-4 p-10 text-center">
            <h1 className="ex-text-brand text-2xl font-bold tracking-tight">
              Unable to reach backend
            </h1>
            <p className="text-sm text-muted-foreground">
              The app couldn't load your session. This is usually transient.
              <br />
              <span className="text-xs">({message})</span>
            </p>
            <Button onClick={() => refetch()} size="lg">
              Retry
            </Button>
          </CardContent>
        </Card>
      </FullScreen>
    )
  }

  if (needsLogin) {
    // No configured IdP, or the one retry failed. Never silently grant access.
    return (
      <FullScreen>
        <Card className="w-full max-w-md">
          <CardContent className="flex flex-col items-center gap-4 p-10 text-center">
            <h1 className="ex-text-brand text-2xl font-bold tracking-tight">Sign-in required</h1>
            <p className="text-sm text-muted-foreground">
              Sign in through your configured identity provider. If this is a local
              instance, use AUTH_MODE=dev with an empty, synthetic database.
            </p>
            {loginUrl && (
              <Button asChild size="lg">
                <a href={loginUrl}>Sign in</a>
              </Button>
            )}
          </CardContent>
        </Card>
      </FullScreen>
    )
  }

  return <Outlet />
}

function RouteError() {
  return (
    <FullScreen>
      <Card className="w-full max-w-md">
        <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
          <h1 className="ex-text-brand text-2xl font-bold tracking-tight">Something went wrong</h1>
          <p className="text-sm text-muted-foreground">
            <a className="underline" href="/">
              Return to the app
            </a>
          </p>
        </CardContent>
      </Card>
    </FullScreen>
  )
}

export const router = createBrowserRouter([
  {
    element: <RequireAuth />,
    errorElement: <RouteError />,
    children: [
      {
        element: <App />,
        children: [
          // The curated marketplace is the landing page; the
          // browse-everything view is its sibling tab.
          { path: '/', element: <MarketplacePage /> },
          { path: '/browse', element: <HomePage /> },
          // `owner/slug` is the reference every hub tool takes, so the detail
          // route mirrors it verbatim under /b/.
          { path: '/b/:owner/:slug', element: <BundleDetailPage /> },
          { path: '/p/:handle', element: <ProfilePage /> },
          { path: '/me', element: <MePage /> },
          // The device flow's verify_url points here with ?code=<user_code>.
          { path: '/connect', element: <ConnectPage /> },
          // Stale / unknown paths (e.g. the old /login) bounce to the app
          // root instead of react-router's default "404 Not Found" error
          // page.
          { path: '*', element: <Navigate to="/" replace /> },
        ],
      },
    ],
  },
])
