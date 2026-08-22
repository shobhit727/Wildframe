'use client';

import { ReactNode, useEffect, useState } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from 'next-themes';
import { useAuthStore } from '@/stores/auth';
import { queryClient } from '@/utils/queryClient';

export function Providers({ children }: { children: ReactNode }) {
  const hydrate = useAuthStore((state) => state.hydrate);
  // Hold the tree until the session check resolves once. Page-level guards
  // read `isAuthenticated` on mount; without this gate every hard navigation
  // briefly looked logged-out and bounced users to /login (middleware then
  // sent them to /browse).
  const [authReady, setAuthReady] = useState(false);

  useEffect(() => {
    hydrate().finally(() => setAuthReady(true));
  }, [hydrate]);

  if (!authReady) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#0b0b0b]">
        <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-red-600" aria-hidden />
      </div>
    );
  }

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
        {children}
      </ThemeProvider>
    </QueryClientProvider>
  );
}
