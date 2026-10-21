/**
 * Selector hooks in `@/hooks`.
 *
 * `useIsAdmin` / `useUser` / `useIsAuthenticated` already have basic coverage
 * in `src/__tests__/auth.test.ts`. What is untested here is the part that
 * actually breaks gates in production: that these hooks re-render when the
 * store changes, and that `useAuth` hands back the action functions.
 */
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return { ...actual, apiClient: { logout: vi.fn().mockResolvedValue(undefined) } };
});

import { useAuth, useIsAdmin, useIsAuthenticated, useRole, useUser } from '@/hooks';
import { useAuthStore } from '@/stores/auth';
import type { User } from '@/types';

function makeUser(role: User['role']): User {
  return {
    id: 'u1',
    email: 'a@b.c',
    firstName: 'A',
    lastName: 'B',
    emailVerified: true,
    role,
  };
}

beforeEach(() => {
  useAuthStore.setState({
    user: null,
    token: null,
    isLoading: false,
    isAuthenticated: false,
    mfaChallenge: null,
  });
});

describe('useRole', () => {
  it('is undefined while signed out', () => {
    const { result } = renderHook(() => useRole());
    expect(result.current).toBeUndefined();
  });

  it('exposes the role of the signed-in user', () => {
    useAuthStore.setState({ user: makeUser('moderator') });
    const { result } = renderHook(() => useRole());
    expect(result.current).toBe('moderator');
  });

  it('re-renders when the role changes in the store', () => {
    const { result } = renderHook(() => useRole());
    expect(result.current).toBeUndefined();

    act(() => useAuthStore.setState({ user: makeUser('admin') }));

    expect(result.current).toBe('admin');
  });
});

describe('useIsAdmin reactivity', () => {
  it('flips to true when an admin signs in', () => {
    const { result } = renderHook(() => useIsAdmin());
    expect(result.current).toBe(false);

    act(() => useAuthStore.setState({ user: makeUser('admin'), isAuthenticated: true }));

    expect(result.current).toBe(true);
  });

  it('flips back to false on sign-out', () => {
    useAuthStore.setState({ user: makeUser('admin'), isAuthenticated: true });
    const { result } = renderHook(() => useIsAdmin());
    expect(result.current).toBe(true);

    act(() => useAuthStore.setState({ user: null, isAuthenticated: false }));

    expect(result.current).toBe(false);
  });

  it('follows a promotion from user to moderator', () => {
    useAuthStore.setState({ user: makeUser('user') });
    const { result } = renderHook(() => useIsAdmin());
    expect(result.current).toBe(false);

    act(() => useAuthStore.setState({ user: makeUser('moderator') }));

    expect(result.current).toBe(true);
  });

  it('does not recompute for unrelated store writes', () => {
    useAuthStore.setState({ user: makeUser('admin') });
    const { result } = renderHook(() => useIsAdmin());
    const before = result.current;

    act(() => useAuthStore.setState({ isLoading: true }));

    expect(result.current).toBe(before);
  });
});

describe('useUser and useIsAuthenticated reactivity', () => {
  it('re-renders useUser when the profile is replaced', () => {
    const { result } = renderHook(() => useUser());
    expect(result.current).toBeNull();

    act(() => useAuthStore.setState({ user: makeUser('user') }));

    expect(result.current?.email).toBe('a@b.c');
  });

  it('re-renders useIsAuthenticated when the session is established', () => {
    const { result } = renderHook(() => useIsAuthenticated());
    expect(result.current).toBe(false);

    act(() => useAuthStore.setState({ isAuthenticated: true, token: 't' }));

    expect(result.current).toBe(true);
  });
});

describe('useAuth', () => {
  it('returns the current auth state alongside the actions', () => {
    useAuthStore.setState({ user: makeUser('admin'), token: 't', isAuthenticated: true });

    const { result } = renderHook(() => useAuth());

    expect(result.current.user?.role).toBe('admin');
    expect(result.current.token).toBe('t');
    expect(result.current.isAuthenticated).toBe(true);
  });

  it('exposes callable actions that drive the store', async () => {
    const { result } = renderHook(() => useAuth());

    act(() => result.current.setUser(makeUser('user')));
    expect(useAuthStore.getState().user?.email).toBe('a@b.c');

    act(() => result.current.setToken('tok'));
    expect(useAuthStore.getState().token).toBe('tok');

    await act(async () => {
      await result.current.logout();
    });
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
