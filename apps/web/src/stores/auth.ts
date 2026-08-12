// Zustand store for auth state.
import { create } from 'zustand';
import { User } from '@/types';
import { apiClient, clearTokens, getAccessToken } from '@/api/client';
import { queryClient } from '@/utils/queryClient';

export interface AuthStore {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  mfaChallenge: string | null;

  login: (email: string, password: string) => Promise<'mfa' | 'ok'>;
  verifyMfa: (code: string) => Promise<void>;
  register: (email: string, password: string, firstName: string, lastName: string) => Promise<void>;
  logout: () => Promise<void>;
  setUser: (user: User | null) => void;
  setToken: (token: string | null) => void;
  hydrate: () => Promise<void>;
  refreshMe: () => Promise<void>;
}

export const useAuthStore = create<AuthStore>((set, get) => ({
  user: null,
  token: null,
  isLoading: false,
  isAuthenticated: false,
  mfaChallenge: null,

  login: async (email: string, password: string) => {
    set({ isLoading: true });
    try {
      const data = await apiClient.login(email, password);
      if ((data as { requires_mfa?: boolean }).requires_mfa) {
        const challenge = (data as { mfa_challenge: string }).mfa_challenge;
        set({ isLoading: false, mfaChallenge: challenge });
        return 'mfa';
      }
      const tokens = data as { access_token: string };
      const user = await apiClient.getMe();
      set({
        token: tokens.access_token,
        user,
        isAuthenticated: true,
        isLoading: false,
        mfaChallenge: null,
      });
      return 'ok';
    } catch (error) {
      set({ isLoading: false });
      throw error;
    }
  },

  verifyMfa: async (code: string) => {
    const challenge = get().mfaChallenge;
    if (!challenge) throw new Error('No MFA challenge in progress');
    set({ isLoading: true });
    try {
      const tokens = await apiClient.verifyMfaLogin(challenge, code);
      const user = await apiClient.getMe();
      set({
        token: tokens.access_token,
        user,
        isAuthenticated: true,
        isLoading: false,
        mfaChallenge: null,
      });
    } catch (error) {
      set({ isLoading: false });
      throw error;
    }
  },

  register: async (email: string, password: string, firstName: string, lastName: string) => {
    set({ isLoading: true });
    try {
      const tokens = await apiClient.register(email, password, firstName, lastName);
      const user = await apiClient.getMe();
      set({
        token: tokens.access_token,
        user,
        isAuthenticated: true,
        isLoading: false,
      });
    } catch (error) {
      set({ isLoading: false });
      throw error;
    }
  },

  logout: async () => {
    try {
      await apiClient.logout();
    } catch (error) {
      console.error('Logout error:', error);
    }
    // Drop all cached query data so the next session starts fresh.
    queryClient.clear();
    set({
      user: null,
      token: null,
      isAuthenticated: false,
      mfaChallenge: null,
    });
  },

  setUser: (user: User | null) => set({ user }),
  setToken: (token: string | null) => set({ token }),

  hydrate: async () => {
    let token = getAccessToken();
    if (!token) {
      token = await apiClient.refreshAccessToken();
      if (!token) {
        set({ user: null, token: null, isAuthenticated: false });
        return;
      }
    }
    set({ token, isAuthenticated: true });
    try {
      const user = await apiClient.getMe();
      set({ user });
    } catch {
      // Token invalid/expired — the interceptor will handle the next 401.
    }
  },

  refreshMe: async () => {
    if (!get().isAuthenticated) return;
    try {
      const user = await apiClient.getMe();
      set({ user });
    } catch {
      // ignore
    }
  },
}));

export function useIsAuthenticated(): boolean {
  return useAuthStore((s) => s.isAuthenticated);
}

export function useUser(): User | null {
  return useAuthStore((s) => s.user);
}