/** Custom React hooks. */
import { useAuthStore } from '@/stores/auth';

export const useAuth = () => {
  const auth = useAuthStore();
  return auth;
};

export const useIsAuthenticated = () => {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  return isAuthenticated;
};

export const useUser = () => {
  const user = useAuthStore((state) => state.user);
  return user;
};

/** True when the authenticated user has an admin-level role. */
export const useIsAdmin = () => {
  const user = useAuthStore((state) => state.user);
  return user?.role === 'admin' || user?.role === 'moderator';
};

export const useRole = () => {
  const user = useAuthStore((state) => state.user);
  return user?.role;
};

export * from './admin';
