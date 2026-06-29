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
      <div className="min-h-screen bg-dark-950 flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 border-2 border-red-600 border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-400 text-sm">Loading...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-dark-950">
      <Navbar onSearchChange={onSearchChange} />
      <main>{children}</main>
      <footer className="mt-20 border-t border-dark-800 py-10 px-4">
        <div className="max-w-6xl mx-auto">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-8 mb-8">
            <div>
              <h4 className="text-sm font-semibold text-gray-400 mb-3">Navigation</h4>
              <ul className="space-y-2 text-sm text-gray-500">
                <li><a href="/browse" className="hover:text-gray-300 transition-colors">Browse</a></li>
                <li><a href="/my-list" className="hover:text-gray-300 transition-colors">My List</a></li>
                <li><a href="/account" className="hover:text-gray-300 transition-colors">Account</a></li>
              </ul>
            </div>
            <div>
              <h4 className="text-sm font-semibold text-gray-400 mb-3">Support</h4>
              <ul className="space-y-2 text-sm text-gray-500">
                <li><a href="#" className="hover:text-gray-300 transition-colors">Help Center</a></li>
                <li><a href="#" className="hover:text-gray-300 transition-colors">Contact</a></li>
              </ul>
            </div>
            <div>
              <h4 className="text-sm font-semibold text-gray-400 mb-3">Legal</h4>
              <ul className="space-y-2 text-sm text-gray-500">
                <li><a href="#" className="hover:text-gray-300 transition-colors">Privacy</a></li>
                <li><a href="#" className="hover:text-gray-300 transition-colors">Terms</a></li>
              </ul>
            </div>
            <div>
              <h4 className="text-sm font-semibold text-gray-400 mb-3">Connect</h4>
              <ul className="space-y-2 text-sm text-gray-500">
                <li><a href="#" className="hover:text-gray-300 transition-colors">Twitter</a></li>
                <li><a href="#" className="hover:text-gray-300 transition-colors">GitHub</a></li>
              </ul>
            </div>
          </div>
          <p className="text-xs text-gray-600 text-center">&copy; {new Date().getFullYear()} Wildframe. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
