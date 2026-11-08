/**
 * Wire types — mirror the backend's pydantic schemas in
 * `backend/src/app/schemas/`. When you change one side, change the other:
 * these are the contract between the SPA and the hub API.
 */

/** `MeOut` — the identity pill's shape (`GET /api/auth/me`). */
export interface User {
  id: string
  email: string
  handle: string
  name: string | null
  display_name: string | null
  avatar_url: string | null
  is_admin: boolean
  created_at: string
  last_sign_in_at: string | null
}

/** `ProfileOut` — a public profile with its two counters. */
export interface Profile {
  handle: string
  email: string
  display_name: string
  avatar_url: string | null
  created_at: string
  bundle_count: number
  import_count: number
  /** An ORG profile (e.g. `example`). Nobody signs in as one — its bundles
   *  are written by the publishers on its roster. */
  is_org: boolean
}

/** `OrgSummaryOut` — an org plus whether the SIGNED-IN user publishes for it. */
export interface OrgSummary {
  profile: Profile
  is_publisher: boolean
  publisher_count: number
}

/** `PublisherOut` — one row of an org's publisher roster. */
export interface Publisher {
  handle: string
  display_name: string
  email: string
  avatar_url: string | null
  added_at: string
  /** Handle of whoever granted this, or null for a seeded grant. */
  added_by: string | null
  /** Invited by email but has never signed in. */
  pending: boolean
}

/** `PublisherListOut` — every publishers endpoint returns this shape, so a
 *  mutation hands back the new roster with no follow-up fetch. */
export interface PublisherList {
  org: Profile
  publishers: Publisher[]
  /** Whether the SIGNED-IN user may change this list. */
  is_publisher: boolean
}

/** One entry of `POST /api/updates/check`'s request. */
export interface InstalledBundle {
  ref: string
  version: number
}

/** `BundleUpdateOut` — a bundle with a newer published version.
 *  Named `AvailableUpdate` here because `BundleUpdate` is already the PATCH
 *  request body, and the two would be easy to mix up at a call site. */
export interface AvailableUpdate {
  ref: string
  latest_version: number
  kind: string
  title: string
  changelog: string
  is_org: boolean
}

export interface UpdateCheck {
  updates: AvailableUpdate[]
  /** Refs that no longer resolve — deleted, and never coming back. */
  missing: string[]
  checked: number
}

/** `ApiTokenOut` — never the secret, only the display prefix. */
export interface ApiToken {
  id: string
  name: string
  prefix: string
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}

export interface MeOverview {
  profile: Profile
  tokens: ApiToken[]
}

/** The one response that carries a token in the clear. */
export interface TokenCreated {
  token: string
  api_token: ApiToken
}

export const BUNDLE_KINDS = [
  'skill',
  'extension',
  'prompt',
  'theme',
  'instructions',
  'settings',
  'keybindings',
  'models',
  'mixed',
] as const

export type BundleKind = (typeof BUNDLE_KINDS)[number]

export const IMPORT_ACTIONS = ['imported', 'enabled', 'disabled', 'removed', 'applied'] as const
export type ImportAction = (typeof IMPORT_ACTIONS)[number]

export interface BundleSummary {
  id: string
  /** `owner/slug` — the reference every hub tool and command takes. */
  ref: string
  owner: string
  owner_display_name: string
  slug: string
  title: string
  description: string
  kind: string
  tags: string[]
  latest_version: number
  install_count: number
  created_at: string
  updated_at: string
}

export interface BundleFileEntry {
  path: string
  size: number
  is_text: boolean
}

export interface BundleFileContent extends BundleFileEntry {
  content_b64: string
}

export interface BundleVersion {
  version: number
  changelog: string
  file_count: number
  total_bytes: number
  created_at: string
  created_by: string | null
  /** The `pi` block from the bundle's package.json, stored verbatim. */
  pi_manifest: Record<string, unknown> | null
}

export interface ImportEvent {
  action: string
  version: number | null
  handle: string
  display_name: string
  created_at: string
}

/** The copyable snippets the Import modal shows. */
export interface ImportSnippets {
  /** `/hub install owner/slug` — the primary one. Tracks the LATEST
   *  version, so `/hub update` and phi's startup check move it forward. */
  phi_install: string
  /** `/hub import owner/slug` — the bare form. Behaves like `phi_install`;
   *  kept because every existing client and doc already says it. */
  phi_command: string
  /** `/hub import owner/slug@vN` — pinned. The bundle stops moving. */
  phi_pin: string
  agent_prompt: string
  curl: string
}

export interface BundleDetail {
  bundle: BundleSummary
  /** Mirror of `bundle.ref`, hoisted for clients that don't nest. */
  ref: string
  /** Mirror of `bundle.latest_version` — what a `v<N>` store path is built from. */
  latest_version: number
  owner_profile: Profile
  versions: BundleVersion[]
  /** File listing for the LATEST version (empty for an unpublished bundle). */
  files: BundleFileEntry[]
  recent_events: ImportEvent[]
  import_snippets: ImportSnippets
  /** The structured instruction blob MCP clients get; shown as reference. */
  import_instructions: Record<string, unknown>
  /** Literally "you are the owner". */
  is_owner: boolean
  /** "You may edit / delete / publish here" — `is_owner`, OR you are a
   *  publisher of the ORG that owns it. This is what gates the UI. */
  can_write: boolean
}

export interface BundleList {
  bundles: BundleSummary[]
  /** Same list as `bundles`, under the alternate envelope key the backend
   *  also serves so a client written to either convention works. */
  items: BundleSummary[]
  total: number
}

export interface ProfileDetail {
  profile: Profile
  bundles: BundleSummary[]
}

export interface VersionDetail {
  ref: string
  version: BundleVersion
  files: BundleFileEntry[]
}

export interface VersionFiles {
  ref: string
  version: number
  pi_manifest: Record<string, unknown> | null
  files: BundleFileContent[]
}

/** What `/connect` shows before the user approves or denies. */
export interface DevicePending {
  user_code: string
  device_name: string
  status: 'pending' | 'approved' | 'denied' | 'expired'
  requested_at: string
  expires_at: string
}

export interface DeviceDecision {
  status: string
  device_name: string
}

export type BundleSort = 'updated' | 'popular'

export interface BundleQuery {
  q?: string
  kind?: string
  owner?: string
  /** Only bundles owned by an org profile — the curated marketplace view. */
  org_only?: boolean
  sort?: BundleSort
}

export interface BundleCreate {
  slug: string
  title: string
  description?: string
  kind?: BundleKind
  tags?: string[]
  /** Publish under an ORG handle instead of yourself. Requires being one
   *  of that org's publishers (403 otherwise, 404 if there is no such org). */
  owner?: string
}

export interface BundleUpdate {
  title?: string
  description?: string
  kind?: BundleKind
  tags?: string[]
}
