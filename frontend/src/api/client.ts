// Centralised env access. Vite exposes only `VITE_`-prefixed vars.
const apiBase = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/$/, '')

export class ApiError extends Error {
  status: number
  body: unknown
  /** For publish rejections (422), the file the hub refused. */
  path?: string
  /** Machine-readable rejection code, e.g. `secret_filename`. */
  code?: string

  constructor(status: number, body: unknown) {
    let message = 'API error'
    let path: string | undefined
    let code: string | undefined

    if (typeof body === 'string') message = body
    else if (body && typeof body === 'object' && 'detail' in body) {
      const detail = (body as { detail?: unknown }).detail
      if (typeof detail === 'string') message = detail
      else if (Array.isArray(detail)) message = detail.map((d) => String(d)).join('; ')
      else if (detail && typeof detail === 'object') {
        // The hub's publish rejections are structured
        // ({error, path, message}) so a client can point at the exact file.
        const d = detail as { error?: unknown; path?: unknown; message?: unknown }
        if (typeof d.message === 'string') message = d.message
        if (typeof d.path === 'string') path = d.path
        if (typeof d.error === 'string') code = d.error
      }
    }
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
    this.path = path
    this.code = code
  }
}

type FetchOptions = Omit<RequestInit, 'body'> & { body?: unknown }

/**
 * Fetch wrapper that:
 *   - prepends VITE_API_BASE_URL (defaults to `/api`)
 *   - sends cookies (`credentials: include`) so session cookies stick
 *   - JSON-encodes the request body unless it's already FormData / Blob
 *   - parses JSON or text on response
 *   - throws ApiError on non-2xx
 */
export async function fetchApi<T = unknown>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const { body, headers, ...rest } = options

  const isFormData = body instanceof FormData
  const isBlob = body instanceof Blob

  const finalHeaders: Record<string, string> = {
    Accept: 'application/json',
    ...((headers as Record<string, string> | undefined) ?? {}),
  }
  if (!isFormData && !isBlob && body !== undefined) {
    finalHeaders['Content-Type'] = 'application/json'
  }

  const finalBody: BodyInit | undefined =
    body === undefined
      ? undefined
      : isFormData || isBlob
        ? (body as BodyInit)
        : JSON.stringify(body)

  const res = await fetch(`${apiBase}${path}`, {
    credentials: 'include',
    headers: finalHeaders,
    body: finalBody,
    ...rest,
  })

  if (res.status === 204) return undefined as T

  const contentType = res.headers.get('content-type') ?? ''
  const payload: unknown = contentType.includes('application/json')
    ? await res.json().catch(() => undefined)
    : await res.text()

  if (!res.ok) throw new ApiError(res.status, payload)

  return payload as T
}
