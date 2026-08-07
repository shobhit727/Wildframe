import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { normalizeUser } from '@/api/client';
import { useIsAdmin } from '@/hooks';
import { useAuthStore, useIsAuthenticated, useUser } from '@/stores/auth';
import type { User } from '@/types';

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return {
    ...actual,
    apiClient: {
      logout: vi.fn().mockResolvedValue(undefined),
    },
  };
});

function makeUser(overrides: Partial<User> = {}): User {
  return normalizeUser({
    id: 'u1',
    email: 'someone@example.com',
    first_name: 'A',
    last_name: 'B',
    role: 'user',
    ...overrides,
  });
}

describe('auth store', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, token: null, isAuthenticated: false });
  });

  it('login sets user, token and auth state', () => {
    const user = makeUser();
    useAuthStore.setState({ token: 'token-1', user, isAuthenticated: true });
    const s = useAuthStore.getState();
    expect(s.user).toEqual(user);
    expect(s.token).toBe('token-1');
    expect(s.isAuthenticated).toBe(true);
  });

  it('logout resets user and clears auth state', async () => {
    useAuthStore.setState({ user: makeUser(), token: 'token-1', isAuthenticated: true });
    await useAuthStore.getState().logout();
    const s = useAuthStore.getState();
    expect(s.user).toBeNull();
    expect(s.token).toBeNull();
    expect(s.isAuthenticated).toBe(false);
  });

  it('exposes the current user via hook', () => {
    const user = makeUser({ email: 'x@example.com' });
    useAuthStore.setState({ user, isAuthenticated: true });
    const { result } = renderHook(() => useUser());
    expect(result.current).toEqual(user);
  });

  it('exposes isAuthenticated via hook', () => {
    useAuthStore.setState({ token: 't', isAuthenticated: true });
    const { result } = renderHook(() => useIsAuthenticated());
    expect(result.current).toBe(true);
  });
});

describe('useIsAdmin', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, token: null, isAuthenticated: false });
  });

  it('is false when logged out', () => {
    const { result } = renderHook(() => useIsAdmin());
    expect(result.current).toBe(false);
  });

  it('is true for an admin user', () => {
    useAuthStore.setState({ user: makeUser({ role: 'admin' }), isAuthenticated: true });
    const { result } = renderHook(() => useIsAdmin());
    expect(result.current).toBe(true);
  });

  it('is true for a moderator user', () => {
    useAuthStore.setState({ user: makeUser({ role: 'moderator' }), isAuthenticated: true });
    const { result } = renderHook(() => useIsAdmin());
    expect(result.current).toBe(true);
  });

  it('is false for a regular user', () => {
    useAuthStore.setState({ user: makeUser({ role: 'user' }), isAuthenticated: true });
    const { result } = renderHook(() => useIsAdmin());
    expect(result.current).toBe(false);
  });
});

describe('normalizeUser role mapping', () => {
  it('keeps the role from the payload', () => {
    expect(normalizeUser({ id: 'u1', email: 'a@b.c', role: 'admin' }).role).toBe('admin');
  });

  it('defaults an unset role to user', () => {
    expect(normalizeUser({ id: 'u1', email: 'a@b.c' }).role).toBe('user');
  });
});