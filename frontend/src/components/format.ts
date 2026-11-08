/**
 * Formatting helpers shared by the hub's pages and cards.
 *
 * Lives under `components/` rather than `lib/` because the frontend brief
 * scopes shared UI code to this directory; it exports no components, so the
 * `react-refresh/only-export-components` rule does not apply to it.
 */

const MINUTE = 60
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/**
 * "3 minutes ago" / "in 12 minutes". Registry timestamps are almost always
 * recent, so a coarse relative string reads faster than a date — the absolute
 * value goes in a `title` attribute at the call site for the rare case where
 * someone needs it.
 */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return 'never'
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return 'unknown'

  const seconds = Math.round((Date.now() - then) / 1000)
  const future = seconds < 0
  const abs = Math.abs(seconds)

  const phrase =
    abs < 45
      ? 'moments'
      : abs < HOUR
        ? plural(Math.round(abs / MINUTE), 'minute')
        : abs < DAY
          ? plural(Math.round(abs / HOUR), 'hour')
          : abs < 30 * DAY
            ? plural(Math.round(abs / DAY), 'day')
            : abs < 365 * DAY
              ? plural(Math.round(abs / (30 * DAY)), 'month')
              : plural(Math.round(abs / (365 * DAY)), 'year')

  if (phrase === 'moments') return future ? 'in a moment' : 'just now'
  return future ? `in ${phrase}` : `${phrase} ago`
}

function plural(n: number, unit: string): string {
  return `${n} ${unit}${n === 1 ? '' : 's'}`
}

/** Full local timestamp — used for `title` tooltips next to relative times. */
export function absoluteTime(iso: string | null | undefined): string {
  if (!iso) return 'never'
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? 'unknown' : new Date(ms).toLocaleString()
}

/** Month + year, for "member since". */
export function monthYear(iso: string | null | undefined): string {
  if (!iso) return 'unknown'
  const ms = Date.parse(iso)
  if (Number.isNaN(ms)) return 'unknown'
  return new Date(ms).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * Decode a base64 file payload into a JS string.
 *
 * `atob` yields a *binary* string — one char per byte — so any multi-byte
 * UTF-8 sequence (an em-dash in a SKILL.md, a box-drawing character in an
 * ASCII diagram) comes back mojibake'd. Round-tripping through a Uint8Array
 * and TextDecoder is what makes the viewer show the file the author wrote.
 */
export function decodeBase64Utf8(b64: string): string {
  return new TextDecoder().decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)))
}

/** Split `a/b/c.md` into its directory (`a/b`, or '' at the root) and name. */
export function splitPath(path: string): { dir: string; name: string } {
  const cut = path.lastIndexOf('/')
  return cut === -1
    ? { dir: '', name: path }
    : { dir: path.slice(0, cut), name: path.slice(cut + 1) }
}
