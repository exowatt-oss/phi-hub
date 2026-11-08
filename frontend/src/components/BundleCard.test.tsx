/**
 * Marketplace card — the one-click install snippet.
 *
 * The marketplace's whole job is handing someone a string to paste, so two
 * things are worth pinning: the string itself (`/hub install owner/slug`,
 * the track-latest form — a card that handed out a pinned command would
 * quietly opt every installer out of updates), and the fact that the copy
 * control is reachable at all. The card stretches its title link across the
 * whole surface with an `::after` pseudo-element, so any interactive child
 * needs its own stacking context or clicking it navigates instead.
 */
import type { ComponentProps } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { BundleSummary } from '@/api/types'
import { BundleCard } from './BundleCard'

const BUNDLE: BundleSummary = {
  id: 'b1',
  ref: 'example/routing-setup',
  owner: 'example',
  owner_display_name: 'example',
  slug: 'routing-setup',
  title: 'Routing setup',
  description: 'Model routing that actually works.',
  kind: 'settings',
  tags: ['routing'],
  latest_version: 3,
  install_count: 12,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
}

function renderCard(props: Partial<ComponentProps<typeof BundleCard>> = {}) {
  return render(
    <MemoryRouter>
      <BundleCard bundle={BUNDLE} {...props} />
    </MemoryRouter>,
  )
}

describe('BundleCard', () => {
  it('offers the track-latest install command when showInstall is set', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })

    renderCard({ showInstall: true })

    const button = screen.getByRole('button', {
      name: /copy install command for example\/routing-setup/i,
    })
    // The visible text is the command, so what you see is what you paste.
    expect(button).toHaveTextContent('/hub install example/routing-setup')

    await userEvent.click(button)
    expect(writeText).toHaveBeenCalledWith('/hub install example/routing-setup')

    vi.unstubAllGlobals()
  })

  it('does not show the install control on the plain card', () => {
    renderCard()
    expect(screen.queryByText(/hub install/)).not.toBeInTheDocument()
  })

  it('does not offer to install a bundle with no published version', () => {
    renderCard({ bundle: { ...BUNDLE, latest_version: 0 }, showInstall: true })
    expect(screen.queryByText(/hub install/)).not.toBeInTheDocument()
    expect(screen.getByText('unpublished')).toBeInTheDocument()
  })
})
