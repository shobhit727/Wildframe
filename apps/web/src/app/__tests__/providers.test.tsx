import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { useQueryClient } from '@tanstack/react-query';

import { Providers } from '@/app/providers';
import { useAuthStore } from '@/stores/auth';

describe('Providers', () => {
  let seenClient: unknown = null;

  beforeEach(() => {
    seenClient = null;
    useAuthStore.setState({
      user: null,
      token: null,
      isAuthenticated: false,
      isLoading: false,
    });
  });

  it('withholds the page tree until the session check resolves', async () => {
    // A gate that renders children first makes every page-level auth guard
    // read isAuthenticated === false on mount and bounce real users to /login.
    let resolveHydrate: () => void = () => {};
    const hydrate = vi.fn(
      () => new Promise<void>((resolve) => {
        resolveHydrate = resolve;
      })
    );
    useAuthStore.setState({ hydrate });

    render(
      <Providers>
        <p>protected content</p>
      </Providers>,
    );

    expect(screen.queryByText('protected content')).toBeNull();
    expect(hydrate).toHaveBeenCalledTimes(1);

    resolveHydrate();

    expect(await screen.findByText('protected content')).toBeInTheDocument();
  });

  it('releases the gate when the session check finds no valid session', async () => {
    // The real store's hydrate() swallows its own failures and resolves with
    // "no session" (user: null, isAuthenticated: false). The gate must open so
    // the page's own auth guard can render the signed-out UI, rather than
    // stranding every logged-out visitor on an endless spinner.
    useAuthStore.setState({ hydrate: vi.fn().mockResolvedValue(undefined) });

    render(
      <Providers>
        <p>signed-out landing</p>
      </Providers>,
    );

    expect(await screen.findByText('signed-out landing')).toBeInTheDocument();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it('hydrates exactly once across re-renders', async () => {
    const hydrate = vi.fn().mockResolvedValue(undefined);
    useAuthStore.setState({ hydrate });

    const { rerender } = render(
      <Providers>
        <p>content</p>
      </Providers>,
    );
    await screen.findByText('content');

    rerender(
      <Providers>
        <p>content</p>
      </Providers>,
    );
    await waitFor(() => expect(screen.getByText('content')).toBeInTheDocument());

    expect(hydrate).toHaveBeenCalledTimes(1);
  });

  it('provides a React Query context to descendants', async () => {
    useAuthStore.setState({ hydrate: vi.fn().mockResolvedValue(undefined) });

    function Probe() {
      // Throws unless an ancestor supplied a QueryClient, so simply rendering
      // without error proves the released tree is wrapped.
      seenClient = useQueryClient();
      return null;
    }

    render(
      <Providers>
        <Probe />
      </Providers>,
    );

    await waitFor(() => expect(seenClient).not.toBeNull());
    expect(seenClient).toBeDefined();
  });
});
