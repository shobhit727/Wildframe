/**
 * Auth store transition tests.
 *
 * `@/api/client` and the shared QueryClient are mocked so no network or global
 * cache is involved; every assertion is on the store's own state machine —
 * which flags move together, and what survives a failed transition.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  login: vi.fn(),
  register: vi.fn(),
  verifyMfaLogin: vi.fn(),
  getMe: vi.fn(),
  logout: vi.fn(),
  refreshAccessToken: vi.fn(),
}));

const queryClientMock = vi.hoisted(() => ({ clear: vi.fn() }));

/** `hydrate()` reads the module-level in-memory token, not store state. */
const getAccessToken = vi.hoisted(() => vi.fn<() => string | null>(() => null));

vi.mock('@/api/client', () => ({
  apiClient: api,
  getAccessToken,
}));

vi.mock('@/utils/queryClient', () => ({ queryClient: queryClientMock }));

import { useAuthStore } from '@/stores/auth';
import type { User } from '@/types';

const ADMIN: User = {
  id: 'u1',
  email: 'ada@example.com',
  firstName: 'Ada',
  lastName: 'Lovelace',
  emailVerified: true,
  role: 'admin',
};

const VIEWER: User = { ...ADMIN, id: 'u2', email: 'bob@example.com', role: 'user' };

const TOKENS = { access_token: 'access-1', refresh_token: 'refresh-1' };

function reset() {
  useAuthStore.setState({
    user: null,
    token: null,
    isLoading: false,
    isAuthenticated: false,
    mfaChallenge: null,
  });
}

beforeEach(() => {
  reset();
  for (const fn of Object.values(api)) fn.mockReset();
  queryClientMock.clear.mockReset();
  getAccessToken.mockReset();
  getAccessToken.mockReturnValue(null);
  api.logout.mockResolvedValue(undefined);
});

afterEach(() => {
  reset();
});

describe('login', () => {
  it('marks the store busy while the request is in flight', async () => {
    let release: (v: unknown) => void = () => {};
    api.login.mockReturnValue(new Promise((r) => (release = r)));
    api.getMe.mockResolvedValue(VIEWER);

    const pending = useAuthStore.getState().login('a@b.c', 'pw');

    expect(useAuthStore.getState().isLoading).toBe(true);
    release(TOKENS);
    await pending;
  });

  it('authenticates and stores the profile on a plain login', async () => {
    api.login.mockResolvedValue(TOKENS);
    api.getMe.mockResolvedValue(VIEWER);

    const outcome = await useAuthStore.getState().login('a@b.c', 'pw');

    expect(outcome).toBe('ok');
    const s = useAuthStore.getState();
    expect(s.token).toBe('access-1');
    expect(s.user).toEqual(VIEWER);
    expect(s.isAuthenticated).toBe(true);
    expect(s.isLoading).toBe(false);
    expect(s.mfaChallenge).toBeNull();
  });

  it('does not fetch the profile before the credential check succeeds', async () => {
    api.login.mockResolvedValue({ requires_mfa: true, mfa_challenge: 'chal-1', expires_in: 300 });

    await useAuthStore.getState().login('a@b.c', 'pw');

    expect(api.getMe).not.toHaveBeenCalled();
  });

  it('stops loading and stays signed out when the credentials are rejected', async () => {
    api.login.mockRejectedValue({ response: { status: 401 } });

    await expect(useAuthStore.getState().login('a@b.c', 'wrong')).rejects.toBeDefined();

    const s = useAuthStore.getState();
    expect(s.isLoading).toBe(false);
    expect(s.isAuthenticated).toBe(false);
    expect(s.token).toBeNull();
  });

  it('leaves a previous session intact when a re-login fails', async () => {
    useAuthStore.setState({ user: ADMIN, token: 'old', isAuthenticated: true });
    api.login.mockRejectedValue(new Error('network down'));

    await expect(useAuthStore.getState().login('a@b.c', 'pw')).rejects.toBeDefined();

    const s = useAuthStore.getState();
    expect(s.token).toBe('old');
    expect(s.isAuthenticated).toBe(true);
    expect(s.isLoading).toBe(false);
  });

  it('leaves the store signed out when the profile lookup fails after a good password', async () => {
    api.login.mockResolvedValue(TOKENS);
    api.getMe.mockRejectedValue(new Error('/me down'));

    await expect(useAuthStore.getState().login('a@b.c', 'pw')).rejects.toBeDefined();

    const s = useAuthStore.getState();
    expect(s.isAuthenticated).toBe(false);
    expect(s.isLoading).toBe(false);
  });
});

describe('MFA login', () => {
  it('parks the store on the MFA branch with the challenge and stays signed out', async () => {
    api.login.mockResolvedValue({ requires_mfa: true, mfa_challenge: 'chal-9', expires_in: 120 });

    const outcome = await useAuthStore.getState().login('a@b.c', 'pw');

    const s = useAuthStore.getState();
    expect(outcome).toBe('mfa');
    expect(s.mfaChallenge).toBe('chal-9');
    expect(s.isAuthenticated).toBe(false);
    expect(s.token).toBeNull();
    expect(s.isLoading).toBe(false);
  });

  it('completes the login with the submitted code', async () => {
    useAuthStore.setState({ mfaChallenge: 'chal-9' });
    api.verifyMfaLogin.mockResolvedValue(TOKENS);
    api.getMe.mockResolvedValue(ADMIN);

    await useAuthStore.getState().verifyMfa('123456');

    const s = useAuthStore.getState();
    expect(s.isAuthenticated).toBe(true);
    expect(s.token).toBe('access-1');
    expect(s.user).toEqual(ADMIN);
    expect(s.mfaChallenge).toBeNull();
  });

  it('refuses to verify without a pending challenge', async () => {
    await expect(useAuthStore.getState().verifyMfa('123456')).rejects.toThrow(
      'No MFA challenge in progress'
    );

    expect(api.verifyMfaLogin).not.toHaveBeenCalled();
  });

  it('keeps the challenge so the user can retype the code after a wrong one', async () => {
    useAuthStore.setState({ mfaChallenge: 'chal-9' });
    api.verifyMfaLogin.mockRejectedValue({ response: { data: { detail: 'Invalid code' } } });

    await expect(useAuthStore.getState().verifyMfa('000000')).rejects.toBeDefined();

    const s = useAuthStore.getState();
    expect(s.mfaChallenge).toBe('chal-9');
    expect(s.isAuthenticated).toBe(false);
    expect(s.isLoading).toBe(false);
  });
});

describe('register', () => {
  it('authenticates and stores the profile after signup', async () => {
    api.register.mockResolvedValue(TOKENS);
    api.getMe.mockResolvedValue(VIEWER);

    await useAuthStore.getState().register('a@b.c', 'pw', 'Ada', 'Lovelace');

    expect(api.register).toHaveBeenCalledWith('a@b.c', 'pw', 'Ada', 'Lovelace');
    const s = useAuthStore.getState();
    expect(s.isAuthenticated).toBe(true);
    expect(s.token).toBe('access-1');
    expect(s.user).toEqual(VIEWER);
  });

  it('stops loading and stays signed out when signup fails', async () => {
    api.register.mockRejectedValue({ response: { data: { detail: 'Email already registered' } } });

    await expect(
      useAuthStore.getState().register('a@b.c', 'pw', 'Ada', 'Lovelace')
    ).rejects.toBeDefined();

    const s = useAuthStore.getState();
    expect(s.isLoading).toBe(false);
    expect(s.isAuthenticated).toBe(false);
  });
});

describe('logout', () => {
  it('clears the cached query data so the next session starts fresh', async () => {
    useAuthStore.setState({ user: ADMIN, token: 't', isAuthenticated: true, mfaChallenge: 'chal' });

    await useAuthStore.getState().logout();

    // Without this, the next user could be served the previous admin's rows.
    expect(queryClientMock.clear).toHaveBeenCalled();
    const s = useAuthStore.getState();
    expect(s.user).toBeNull();
    expect(s.token).toBeNull();
    expect(s.isAuthenticated).toBe(false);
    expect(s.mfaChallenge).toBeNull();
  });

  it('leaves the session in place when the server-side revoke fails', async () => {
    useAuthStore.setState({ user: ADMIN, token: 't', isAuthenticated: true });
    api.logout.mockRejectedValue(new Error('Could not end session'));

    await expect(useAuthStore.getState().logout()).rejects.toBeDefined();

    // The user stays signed in locally, so the UI must not pretend otherwise.
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(queryClientMock.clear).not.toHaveBeenCalled();
  });
});

describe('hydrate', () => {
  it('adopts the in-memory token without asking the session route', async () => {
    getAccessToken.mockReturnValue('memory-token');
    api.getMe.mockResolvedValue(ADMIN);

    await useAuthStore.getState().hydrate();

    expect(api.refreshAccessToken).not.toHaveBeenCalled();
    const s = useAuthStore.getState();
    expect(s.token).toBe('memory-token');
    expect(s.isAuthenticated).toBe(true);
    expect(s.user).toEqual(ADMIN);
    expect(s.isLoading).toBe(false);
  });

  it('recovers a session from the HttpOnly refresh cookie when memory is empty', async () => {
    api.refreshAccessToken.mockResolvedValue('cookie-token');
    api.getMe.mockResolvedValue(ADMIN);

    await useAuthStore.getState().hydrate();

    const s = useAuthStore.getState();
    expect(s.token).toBe('cookie-token');
    expect(s.isAuthenticated).toBe(true);
    expect(s.user).toEqual(ADMIN);
  });

  it('marks hydration busy before awaiting so role guards wait for /me', async () => {
    let release: (v: unknown) => void = () => {};
    api.refreshAccessToken.mockReturnValue(new Promise((r) => (release = r)));

    const pending = useAuthStore.getState().hydrate();

    expect(useAuthStore.getState().isLoading).toBe(true);
    release(null);
    await pending;
  });

  it('stays signed out and does not call /me when no session can be recovered', async () => {
    api.refreshAccessToken.mockResolvedValue(null);

    await useAuthStore.getState().hydrate();

    const s = useAuthStore.getState();
    expect(s.isAuthenticated).toBe(false);
    expect(s.token).toBeNull();
    expect(s.user).toBeNull();
    expect(s.isLoading).toBe(false);
    expect(api.getMe).not.toHaveBeenCalled();
  });

  it('keeps the session authenticated when /me fails but the token is held', async () => {
    api.refreshAccessToken.mockResolvedValue('cookie-token');
    api.getMe.mockRejectedValue(new Error('500'));

    await useAuthStore.getState().hydrate();

    // The token is good; only the profile fetch failed. Flipping
    // isAuthenticated here would bounce a signed-in user to /login.
    const s = useAuthStore.getState();
    expect(s.isAuthenticated).toBe(true);
    expect(s.isLoading).toBe(false);
  });
});

describe('refreshMe', () => {
  it('does nothing while signed out', async () => {
    useAuthStore.setState({ isAuthenticated: false, user: ADMIN });

    await useAuthStore.getState().refreshMe();

    expect(api.getMe).not.toHaveBeenCalled();
    expect(useAuthStore.getState().user).toEqual(ADMIN);
  });

  it('replaces the cached profile with the fresh one', async () => {
    useAuthStore.setState({ isAuthenticated: true, user: VIEWER });
    api.getMe.mockResolvedValue({ ...VIEWER, email: 'ada@example.com' });

    await useAuthStore.getState().refreshMe();

    expect(useAuthStore.getState().user?.email).toBe('ada@example.com');
  });

  it('keeps the stale profile when the refresh fails', async () => {
    useAuthStore.setState({ isAuthenticated: true, user: VIEWER });
    api.getMe.mockRejectedValue(new Error('offline'));

    await expect(useAuthStore.getState().refreshMe()).resolves.toBeUndefined();

    expect(useAuthStore.getState().user).toEqual(VIEWER);
  });
});

describe('plain setters', () => {
  it('replaces the user', () => {
    useAuthStore.getState().setUser(ADMIN);
    expect(useAuthStore.getState().user).toEqual(ADMIN);

    useAuthStore.getState().setUser(null);
    expect(useAuthStore.getState().user).toBeNull();
  });

  it('replaces the token', () => {
    useAuthStore.getState().setToken('abc');
    expect(useAuthStore.getState().token).toBe('abc');

    useAuthStore.getState().setToken(null);
    expect(useAuthStore.getState().token).toBeNull();
  });

  it('does not flip isAuthenticated on its own when the token is set', () => {
    useAuthStore.getState().setToken('abc');
    // Authentication is a derived decision, not a side effect of holding a
    // token; otherwise a bare setToken would grant a session.
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
