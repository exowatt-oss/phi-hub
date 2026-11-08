import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, fetchApi } from './client'
import type {
  BundleCreate,
  BundleDetail,
  BundleList,
  BundleQuery,
  BundleSummary,
  BundleUpdate,
  DeviceDecision,
  DevicePending,
  ImportAction,
  InstalledBundle,
  MeOverview,
  OrgSummary,
  Profile,
  ProfileDetail,
  PublisherList,
  TokenCreated,
  UpdateCheck,
  User,
  VersionFiles,
} from './types'

/* -------------------------------------------------------------------------
 * Query keys. Centralised so a mutation can invalidate precisely what it
 * changed instead of nuking the whole cache.
 * ---------------------------------------------------------------------- */
export const keys = {
  me: ['me'] as const,
  overview: ['me', 'overview'] as const,
  profiles: ['profiles'] as const,
  profile: (handle: string) => ['profiles', handle] as const,
  orgs: ['orgs'] as const,
  publishers: (handle: string) => ['orgs', handle, 'publishers'] as const,
  bundles: (q: BundleQuery) => ['bundles', q] as const,
  bundle: (owner: string, slug: string) => ['bundle', owner, slug] as const,
  versionFiles: (owner: string, slug: string, version: string | number) =>
    ['bundle', owner, slug, 'files', String(version)] as const,
  devicePending: (code: string) => ['device', code] as const,
}

/* ------------------------------------------------------------------ auth */

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: async (): Promise<User | null> => {
      try {
        return await fetchApi<User>('/auth/me')
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null
        throw err
      }
    },
    retry: false,
    staleTime: 60_000,
  })
}

/**
 * Sign out through the operator-configured identity provider. The backend returns a logout URL. Without AUTH_LOGOUT_URL, the fallback only returns to the app; it does not clear an external IdP session.
 */
export async function logout(): Promise<void> {
  const data = await fetchApi<{ logout_url: string }>('/auth/logout')
  window.location.href = data.logout_url
}

/* --------------------------------------------------------------- my stuff */

/** Profile + token list. */
export function useMyOverview() {
  return useQuery({
    queryKey: keys.overview,
    queryFn: () => fetchApi<MeOverview>('/me'),
  })
}

export function useUpdateDisplayName() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (display_name: string | null) =>
      fetchApi<MeOverview>('/me', { method: 'PATCH', body: { display_name } }),
    onSuccess: (data) => {
      qc.setQueryData(keys.overview, data)
      // The sidebar pill reads /auth/me, which carries display_name too.
      void qc.invalidateQueries({ queryKey: keys.me })
    },
  })
}

export function useMintToken() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) =>
      fetchApi<TokenCreated>('/tokens', { method: 'POST', body: { name } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.overview }),
  })
}

export function useRevokeToken() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => fetchApi(`/tokens/${id}`, { method: 'DELETE' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.overview }),
  })
}

/* -------------------------------------------------------------- profiles */

export function useProfiles() {
  return useQuery({ queryKey: keys.profiles, queryFn: () => fetchApi<Profile[]>('/profiles') })
}

export function useProfile(handle: string | undefined) {
  return useQuery({
    queryKey: keys.profile(handle ?? ''),
    queryFn: () => fetchApi<ProfileDetail>(`/profiles/${handle}`),
    enabled: Boolean(handle),
  })
}

/* ------------------------------------------------------------------ orgs */

/** Every org, with whether the signed-in user may publish for it. Drives
 *  the "publish as" picker in the new-bundle dialog. */
export function useOrgs() {
  return useQuery({ queryKey: keys.orgs, queryFn: () => fetchApi<OrgSummary[]>('/orgs') })
}

/**
 * An org's publisher roster. Enabled only for org profiles — asking for a
 * person's handle would 404, and a 404 in the query cache would render as
 * an error card on an otherwise fine profile page.
 *
 * The response carries `is_publisher` for the signed-in user, which is how
 * the page decides whether to show the add/remove controls without
 * re-deriving the rule client-side.
 */
export function useOrgPublishers(handle: string | undefined, enabled = true) {
  return useQuery({
    queryKey: keys.publishers(handle ?? ''),
    queryFn: () => fetchApi<PublisherList>(`/orgs/${handle}/publishers`),
    enabled: enabled && Boolean(handle),
  })
}

/** Grant publish rights by email. The stub profile is created server-side
 *  if that person has never touched the hub. */
export function useAddPublisher(handle: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (email: string) =>
      fetchApi<PublisherList>(`/orgs/${handle}/publishers`, { method: 'POST', body: { email } }),
    // Every publishers endpoint returns the NEW roster, so seed the cache
    // from the response rather than round-tripping again.
    onSuccess: (data) => {
      qc.setQueryData(keys.publishers(handle), data)
      void qc.invalidateQueries({ queryKey: keys.profiles })
    },
  })
}

export function useRemovePublisher(handle: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (userHandle: string) =>
      fetchApi<PublisherList>(`/orgs/${handle}/publishers/${userHandle}`, { method: 'DELETE' }),
    onSuccess: (data) => qc.setQueryData(keys.publishers(handle), data),
  })
}

/**
 * Batch update check. The web UI does not install anything, so nothing
 * calls this today — it exists so the SPA and the phi client are built
 * against the same typed surface, and so a future "your hub is N versions
 * behind" panel is a render away rather than an API change.
 */
export async function checkUpdates(installed: InstalledBundle[]): Promise<UpdateCheck> {
  return fetchApi<UpdateCheck>('/updates/check', { method: 'POST', body: { installed } })
}

/* --------------------------------------------------------------- bundles */

export function useBundles(query: BundleQuery) {
  return useQuery({
    queryKey: keys.bundles(query),
    queryFn: () => {
      const params = new URLSearchParams()
      if (query.q) params.set('q', query.q)
      if (query.kind) params.set('kind', query.kind)
      if (query.owner) params.set('owner', query.owner)
      if (query.org_only) params.set('org_only', 'true')
      params.set('sort', query.sort ?? 'updated')
      return fetchApi<BundleList>(`/bundles?${params.toString()}`)
    },
    // Keeps the previous list on screen while a new search resolves, so
    // typing in the search box doesn't strobe the grid.
    placeholderData: (prev) => prev,
  })
}

export function useBundle(owner: string | undefined, slug: string | undefined) {
  return useQuery({
    queryKey: keys.bundle(owner ?? '', slug ?? ''),
    queryFn: () => fetchApi<BundleDetail>(`/bundles/${owner}/${slug}`),
    enabled: Boolean(owner && slug),
  })
}

/** Lazily fetched only when the user opens a text file in the viewer. */
export function useVersionFiles(
  owner: string | undefined,
  slug: string | undefined,
  version: string | number,
  enabled: boolean,
) {
  return useQuery({
    queryKey: keys.versionFiles(owner ?? '', slug ?? '', version),
    queryFn: () => fetchApi<VersionFiles>(`/bundles/${owner}/${slug}/versions/${version}/files`),
    enabled: enabled && Boolean(owner && slug),
    staleTime: 5 * 60_000,
  })
}

export function useCreateBundle() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: BundleCreate) =>
      fetchApi<BundleSummary>('/bundles', { method: 'POST', body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['bundles'] })
      void qc.invalidateQueries({ queryKey: keys.overview })
    },
  })
}

export function useUpdateBundle(owner: string, slug: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: BundleUpdate) =>
      fetchApi<BundleDetail>(`/bundles/${owner}/${slug}`, { method: 'PATCH', body }),
    onSuccess: (data) => {
      qc.setQueryData(keys.bundle(owner, slug), data)
      void qc.invalidateQueries({ queryKey: ['bundles'] })
    },
  })
}

export function useDeleteBundle() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ owner, slug }: { owner: string; slug: string }) =>
      fetchApi(`/bundles/${owner}/${slug}`, { method: 'DELETE' }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['bundles'] })
      void qc.invalidateQueries({ queryKey: ['bundle'] })
      void qc.invalidateQueries({ queryKey: keys.overview })
    },
  })
}

/**
 * Import telemetry. The web UI can't actually install anything into the
 * user's local phi — but "I took this" is still worth recording when
 * someone copies the snippet, and it's what makes the install count on a
 * card mean something.
 */
export function useRecordImport(owner: string, slug: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ action, version }: { action: ImportAction; version?: number }) =>
      fetchApi(`/bundles/${owner}/${slug}/events`, {
        method: 'POST',
        body: { action, version: version ?? null },
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.bundle(owner, slug) }),
  })
}

/** Publish version N+1. Files arrive already base64-encoded. */
export function usePublishVersion(owner: string, slug: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      changelog?: string
      files: { path: string; content_b64: string }[]
    }) => fetchApi(`/bundles/${owner}/${slug}/versions`, { method: 'POST', body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.bundle(owner, slug) })
      void qc.invalidateQueries({ queryKey: ['bundles'] })
    },
  })
}

/* ----------------------------------------------------------- device flow */

export function useDevicePending(code: string | null) {
  return useQuery({
    queryKey: keys.devicePending(code ?? ''),
    queryFn: () => fetchApi<DevicePending>(`/auth/device/pending?code=${encodeURIComponent(code!)}`),
    enabled: Boolean(code),
    retry: false,
  })
}

export function useDeviceDecision(code: string | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (decision: 'approve' | 'deny') =>
      fetchApi<DeviceDecision>(`/auth/device/${decision}`, {
        method: 'POST',
        body: { user_code: code },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.devicePending(code ?? '') })
      void qc.invalidateQueries({ queryKey: keys.overview })
    },
  })
}

export type { User }
