import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import AdminLayout from '@/app/admin/layout';
import { useAuthStore } from '@/stores/auth';
import { makeAdminUser, makeUser } from '@/__tests__/app-helpers';

const { nav, push, replace } = vi.hoisted(() => ({
  nav: { pathname: '/admin' },
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace, refresh: vi.fn() }),
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const NAV_LINKS = [
  { href: '/admin', name: 'Dashboard' },
  { href: '/admin/users', name: 'Users' },
  { href: '/admin/flags', name: 'Content Flags' },
  { href: '/admin/alerts', name: 'Alerts' },
  { href: '/admin/config', name: 'System Config' },
  { href: '/admin/audit', name: 'Audit Log' },
];

function sidebar(): HTMLElement {
  return screen.getByRole('navigation').closest('aside') as HTMLElement;
}

/**
 * A sidebar entry, located structurally. getByRole would rebuild the
 * accessibility tree for the whole console (six links, an identity block and
 * the mounted children) just to match one link's accessible name.
 */
function navLink(name: string): HTMLAnchorElement {
  const nav = screen.getByRole('navigation');
  const link = Array.from(nav.querySelectorAll('a')).find(
    (a) => (a.textContent ?? '').trim() === name,
  );
  if (!link) throw new Error(`nav link "${name}" not found`);
  return link as HTMLAnchorElement;
}

beforeEach(() => {
  nav.pathname = '/admin';
  push.mockReset();
  replace.mockReset();
  useAuthStore.setState({
    user: makeAdminUser(),
    token: 'tok',
    isAuthenticated: true,
    isLoading: false,
    logout: vi.fn().mockResolvedValue(undefined),
  });
});

describe('AdminLayout (AdminGate)', () => {
  describe('access control', () => {
    it('redirects an anonymous visitor to sign in', async () => {
      useAuthStore.setState({ user: null, token: null, isAuthenticated: false, isLoading: false });

      renderGate();

      expect(await screen.findByText('Redirecting to sign in…')).toBeInTheDocument();
      expect(replace).toHaveBeenCalledWith('/login');
    });

    it('withholds the console from an anonymous visitor', async () => {
      useAuthStore.setState({ user: null, token: null, isAuthenticated: false, isLoading: false });

      renderGate();

      await screen.findByText('Redirecting to sign in…');
      expect(screen.queryByRole('navigation')).toBeNull();
      expect(document.querySelector('a[href="/admin/users"]')).toBeNull();
    });

    it('sends an authenticated non-admin to their account page', async () => {
      useAuthStore.setState({
        user: makeUser(),
        token: 'tok',
        isAuthenticated: true,
        isLoading: false,
      });

      renderGate();

      expect(await screen.findByText('Checking permissions…')).toBeInTheDocument();
      expect(replace).toHaveBeenCalledWith('/account');
    });

    it('does not leak moderation tables to a non-admin', async () => {
      useAuthStore.setState({
        user: makeUser(),
        token: 'tok',
        isAuthenticated: true,
        isLoading: false,
      });

      renderGate();

      await screen.findByText('Checking permissions…');
      expect(screen.queryByText('users-api-404')).toBeNull();
    });

    it('waits for the role to resolve before deciding', async () => {
      // /me is still in flight: authenticated but the role is unknown.
      // Redirecting now would bounce a real admin to /login or /account before
      // the role was ever known.
      useAuthStore.setState({ user: null, token: 'tok', isAuthenticated: true, isLoading: true });

      renderGate();

      expect(await screen.findByText('Checking permissions…')).toBeInTheDocument();
      expect(replace).not.toHaveBeenCalled();
    });

    it('admins the console for a user with the admin role', async () => {
      renderGate();

      expect(await screen.findByRole('navigation')).toBeInTheDocument();
      expect(replace).not.toHaveBeenCalled();
    });

    it('admins the console for a moderator', async () => {
      useAuthStore.setState({
        user: makeUser({ role: 'moderator' }),
        token: 'tok',
        isAuthenticated: true,
        isLoading: false,
      });

      renderGate();

      expect(await screen.findByRole('navigation')).toBeInTheDocument();
      expect(replace).not.toHaveBeenCalled();
    });
  });

  describe('navigation', () => {
    it('links to every admin section', async () => {
      renderGate();
      const navEl = await screen.findByRole('navigation');

      // A single structural pass: naming every link through getByRole means
      // six accessible-name computations over the whole console sidebar.
      const targets = new Map(
        Array.from(navEl.querySelectorAll('a')).map((a) => [
          (a.textContent ?? '').trim(),
          a.getAttribute('href'),
        ]),
      );

      for (const link of NAV_LINKS) {
        expect(targets.get(link.name)).toBe(link.href);
      }
    });

    it('marks the current section as active', async () => {
      nav.pathname = '/admin/flags';

      renderGate();
      await screen.findByRole('navigation');

      expect(navLink('Content Flags')).toHaveClass('text-red-400');
      expect(navLink('Users')).toHaveClass('text-zinc-400');
    });

    it('does not mark Dashboard active on a nested route', async () => {
      nav.pathname = '/admin/users';

      renderGate();
      await screen.findByRole('navigation');

      // The dashboard entry must only light up on the exact route, otherwise
      // two items read as current on every nested page.
      expect(navLink('Dashboard')).toHaveClass('text-zinc-400');
      expect(navLink('Users')).toHaveClass('text-red-400');
    });

    it('starts with the mobile drawer closed', async () => {
      renderGate();
      await screen.findByRole('navigation');

      expect(sidebar()).toHaveClass('-translate-x-full');
    });

    it('opens and closes the mobile drawer', async () => {
      renderGate();
      await screen.findByRole('navigation');

      fireEvent.click(screen.getByRole('button', { name: 'Toggle navigation' }));
      expect(sidebar()).toHaveClass('translate-x-0');

      fireEvent.click(screen.getByRole('button', { name: 'Toggle navigation' }));
      expect(sidebar()).toHaveClass('-translate-x-full');
    });

    it('closes the drawer when a destination is chosen', async () => {
      renderGate();
      await screen.findByRole('navigation');

      fireEvent.click(screen.getByRole('button', { name: 'Toggle navigation' }));
      expect(sidebar()).toHaveClass('translate-x-0');

      fireEvent.click(navLink('Users'));

      // Leaving the drawer open over the destination page traps a mobile
      // visitor behind a full-height overlay.
      await waitFor(() => expect(sidebar()).toHaveClass('-translate-x-full'));
    });
  });

  describe('identity', () => {
    it('shows the signed-in admin and their email', async () => {
      renderGate();
      await screen.findByRole('navigation');

      expect(screen.getAllByText('Grace Hopper').length).toBeGreaterThan(0);
      expect(screen.getByText('ada@wildframe.test')).toBeInTheDocument();
    });

    it('renders the admin initial in both avatar slots', async () => {
      renderGate();
      await screen.findByRole('navigation');

      expect(screen.getAllByText('GH').length).toBeGreaterThan(0);
    });
  });

  describe('sign out', () => {
    it('returns to sign in after a successful logout', async () => {
      const logout = vi.fn().mockResolvedValue(undefined);
      useAuthStore.setState({ logout });
      renderGate();
      await screen.findByRole('navigation');

      fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));

      await waitFor(() => expect(push).toHaveBeenCalledWith('/login'));
      expect(logout).toHaveBeenCalledTimes(1);
    });

    it('keeps the admin signed in when logout fails', async () => {
      const logout = vi.fn().mockRejectedValue(new Error('logout 500'));
      useAuthStore.setState({ logout });
      renderGate();
      await screen.findByRole('navigation');

      fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));

      // Navigating away after a failed logout would leave the session cookie
      // live while the UI claims to be signed out.
      await waitFor(() => expect(logout).toHaveBeenCalled());
      expect(push).not.toHaveBeenCalled();
      expect(screen.getByRole('navigation')).toBeInTheDocument();
    });
  });
});

/** Render the layout with a child page stub, mirroring the real route tree. */
function renderGate(child = <p>users-api-404</p>) {
  return render(<AdminLayout>{child}</AdminLayout>);
}
