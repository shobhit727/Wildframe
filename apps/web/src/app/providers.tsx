'use client';

import { ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from 'next-themes';
import { useAuthStore } from '@/stores/auth';
import { useEffect } from 'react';
import { queryClient } from '@/utils/queryClient';

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
