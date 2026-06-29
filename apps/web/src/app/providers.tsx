'use client';

import { ReactNode } from 'react';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';
import { ThemeProvider } from 'next-themes';
import { useAuthStore } from '@/stores/auth';
import { useEffect } from 'react';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000,
      gcTime: 10 * 60 * 1000,
      retry: 2,
    },
  },
});

export function Providers({ children }: { children: ReactNode }) {
  const hydrate = useAuthStore((state) => state.hydrate);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
        {children}
      </ThemeProvider>
    </QueryClientProvider>
  );
}
