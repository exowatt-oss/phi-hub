import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

import { RequireAuth } from './router'
import { useAuth } from '@/lib/auth'
import type { User } from '@/api/types'

vi.mock('@/lib/auth', () => ({ useAuth: vi.fn() }))
const mockedUseAuth = vi.mocked(useAuth)
const HREF = 'https://hub.example.org/some/page'
const EXPECTED_SIGN_IN = `https://login.example.org/start?rd=${encodeURIComponent(HREF)}`
const USER: User = {
  id: 'u1', email: 'operator@example.org', handle: 'operator', name: 'Operator',
  display_name: 'Operator', avatar_url: null, is_admin: false,
  created_at: '2026-01-01T00:00:00Z', last_sign_in_at: null,
}
let replaceSpy: ReturnType<typeof vi.fn>

beforeEach(() => {
  sessionStorage.clear()
  replaceSpy = vi.fn()
  vi.stubGlobal('location', { href: HREF, replace: replaceSpy })
  vi.stubEnv('VITE_SIGN_IN_URL', 'https://login.example.org/start')
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  vi.clearAllMocks()
})

function setAuth(value: Partial<ReturnType<typeof useAuth>>) {
  mockedUseAuth.mockReturnValue({
    user: null, isLoading: false, isError: false, error: null,
    isAuthenticated: false, refetch: vi.fn(), ...value,
  } as ReturnType<typeof useAuth>)
}
function renderGuard() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route element={<RequireAuth />}>
          <Route index element={<div>protected content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('RequireAuth', () => {
  it('redirects only once when an IdP URL is configured', () => {
    setAuth({ user: null })
    renderGuard()
    expect(replaceSpy).toHaveBeenCalledOnce()
    expect(replaceSpy).toHaveBeenCalledWith(EXPECTED_SIGN_IN)
    expect(sessionStorage.getItem('app_sso_retry')).toBe('1')
  })

  it('shows a manual sign-in card after the single redirect', () => {
    sessionStorage.setItem('app_sso_retry', '1')
    setAuth({ user: null })
    renderGuard()
    expect(replaceSpy).not.toHaveBeenCalled()
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', EXPECTED_SIGN_IN)
  })

  it('never redirects when no IdP is configured', () => {
    vi.stubEnv('VITE_SIGN_IN_URL', '')
    setAuth({ user: null })
    renderGuard()
    expect(replaceSpy).not.toHaveBeenCalled()
    expect(screen.getByText('Sign-in required')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Sign in' })).not.toBeInTheDocument()
  })

  it('does not redirect on backend failure', () => {
    setAuth({ user: null, isError: true, error: new Error('boom') })
    renderGuard()
    expect(replaceSpy).not.toHaveBeenCalled()
    expect(screen.getByText(/couldn't load your session/i)).toBeInTheDocument()
  })

  it('clears retry guard after authentication', () => {
    sessionStorage.setItem('app_sso_retry', '1')
    setAuth({ user: USER, isAuthenticated: true })
    renderGuard()
    expect(screen.getByText('protected content')).toBeInTheDocument()
    expect(sessionStorage.getItem('app_sso_retry')).toBeNull()
  })
})
