import { QueryClient } from '@tanstack/react-query';

/**
 * Shared QueryClient instance.
 * Exported separately to avoid circular imports between providers.tsx
 * and the auth store (which needs to clear the cache on logout).
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000,
      gcTime: 10 * 60 * 1000,
      retry: 2,
    },
  },
});