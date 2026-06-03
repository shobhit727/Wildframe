'use client';

import Link from 'next/link';
import { useAuth } from '@/hooks';
import { useRouter } from 'next/navigation';

export function Header() {
  const { user, logout, isAuthenticated } = useAuth();
  const router = useRouter();

  const handleLogout = async () => {
    await logout();
    router.push('/login');
  };

  return (
    <header className="bg-black text-white sticky top-0 z-50 border-b border-gray-800">
      <nav className="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
        <Link href="/" className="text-2xl font-bold text-red-600">
          WILDFRAME
        </Link>
        
        <div className="hidden md:flex gap-8">
          <Link href="/browse" className="hover:text-red-600 transition">Browse</Link>
          <Link href="/my-list" className="hover:text-red-600 transition">My List</Link>
          <Link href="/downloads" className="hover:text-red-600 transition">Downloads</Link>
        </div>

        <div className="flex items-center gap-4">
          {isAuthenticated && user && (
            <>
              <span className="text-sm">{user.firstName}</span>
              <button
                onClick={handleLogout}
                className="bg-red-600 px-4 py-2 rounded hover:bg-red-700 transition"
              >
                Logout
              </button>
            </>
          )}
          {!isAuthenticated && (
            <Link
              href="/login"
              className="bg-red-600 px-4 py-2 rounded hover:bg-red-700 transition"
            >
              Sign In
            </Link>
          )}
        </div>
      </nav>
    </header>
  );
}
