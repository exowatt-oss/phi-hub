/**
 * Hub vocabulary shared by components — the strings and clipboard plumbing
 * that more than one surface needs.
 *
 * Lives outside the component files because both are imported by two of
 * them, and eslint's `react-refresh/only-export-components` (rightly)
 * refuses to let a component module double as a utility module.
 */

/**
 * The command that installs a bundle and keeps it on the latest version.
 *
 * Mirrors `import_snippets.phi_install` from the API. Duplicated here on
 * purpose: a card in a list has only a `BundleSummary`, and fetching the
 * full detail payload per card just to read one string would turn one
 * request into a screenful.
 */
export function installCommand(bundle: { owner: string; slug: string }): string {
  return `/hub install ${bundle.owner}/${bundle.slug}`
}

/**
 * Copy to the clipboard, with a fallback.
 *
 * The clipboard API is unavailable outside a secure context (plain-HTTP dev
 * hosts, some embedded browsers). Falling back to a detached textarea +
 * execCommand keeps the copy buttons working there rather than failing
 * silently — handing the user a string to paste is the whole job of every
 * caller.
 */
export async function copyText(value: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
      return true
    }
  } catch {
    // fall through to the legacy path
  }
  try {
    const el = document.createElement('textarea')
    el.value = value
    el.setAttribute('readonly', '')
    el.style.position = 'fixed'
    el.style.opacity = '0'
    document.body.appendChild(el)
    el.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(el)
    return ok
  } catch {
    return false
  }
}
