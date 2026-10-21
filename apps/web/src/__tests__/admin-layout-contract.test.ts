/**
 * The admin console's <main> has to opt out of the flex item's automatic
 * minimum size.
 *
 * `<main className="flex-1">` sits inside `<div className="flex">`. A flex
 * item's default `min-width` is `auto`, so it refuses to shrink below the
 * admin tables' intrinsic width -- the whole document then scrolls sideways
 * on a phone (measured scrollWidth 413/446/515/538 against a 390px viewport,
 * 23/56/125/148px of overflow). `min-w-0` on the <main> is what fixes it.
 *
 * jsdom has no layout engine, so the overflow cannot be measured here; the
 * contract is asserted structurally on the rendered element instead. The
 * sibling assertions matter as much as `min-w-0` itself: the utility is only
 * load-bearing while <main> stays a flex item of a flex row.
 */
import { createElement } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import AdminLayout from '@/app/admin/layout';
import { useAuthStore } from '@/stores/auth';
import { makeAdminUser } from '@/__tests__/app-helpers';

const { push, replace } = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace, refresh: vi.fn() }),
  usePathname: () => '/admin',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

beforeEach(() => {
  useAuthStore.setState({
    user: makeAdminUser(),
    token: 'tok',
    isAuthenticated: true,
    isLoading: false,
    logout: vi.fn().mockResolvedValue(undefined),
  });
});

/** Render the console with a child page stub, mirroring the real route tree. */
async function renderConsole(): Promise<HTMLElement> {
  const { container } = render(
    createElement(AdminLayout, null, createElement('p', null, 'admin content')),
  );
  await screen.findByRole('navigation');
  const main = container.querySelector('main');
  if (!main) throw new Error('the admin layout rendered no <main> element');
  return main;
}

describe('admin layout overflow contract', () => {
  it('lets the main column shrink below the admin tables intrinsic width', async () => {
    const main = await renderConsole();

    expect(
      main,
      'the admin <main> must carry `min-w-0`: a flex item\'s default min-width is auto, so without it the admin tables push the whole document into horizontal overflow on a phone',
    ).toHaveClass('min-w-0');
  });

  it('keeps the main column a flex item of a flex row', async () => {
    const main = await renderConsole();

    // Without a flex parent and a flex-1 basis, `min-w-0` is inert and the
    // overflow can come back through another route.
    expect(main).toHaveClass('flex-1');
    expect(main.parentElement).toHaveClass('flex');
  });
});
