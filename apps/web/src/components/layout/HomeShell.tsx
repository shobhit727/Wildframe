'use client';

import { ReactNode } from 'react';
import { Navbar } from './Navbar';
import { useIsAuthenticated } from '@/hooks';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

interface HomeShellProps {
  children: ReactNode;
  onSearchChange?: (query: string) => void;
  requireAuth?: boolean;
}

export function HomeShell({ children, onSearchChange, requireAuth = true }: HomeShellProps) {
  const isAuthenticated = useIsAuthenticated();
  const router = useRouter();

  useEffect(() => {
    if (requireAuth && !isAuthenticated) {
      router.push('/login');
    }
  }, [isAuthenticated, router, requireAuth]);

  if (requireAuth && !isAuthenticated) {
    return (
      <div className="min-h-screen bg-[#141414] flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 border-2 border-[#E50914] border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-400 text-sm">Loading...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#141414] text-white">
      <Navbar onSearchChange={onSearchChange} />
      <main>{children}</main>
    </div>
  );
}