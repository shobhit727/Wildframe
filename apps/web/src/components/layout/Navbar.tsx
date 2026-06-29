'use client';

import Link from 'next/link';
import { useAuth } from '@/hooks';
import { useRouter } from 'next/navigation';
import { useState, useRef, useEffect } from 'react';
import { useTheme } from 'next-themes';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';

// Inline SVG icons to avoid adding deps
function SearchIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
    </svg>
  );
}

function BellIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.15a2 2 0 01-2.828 0M18 8V6a6 6 0 00-12 0v2a6 6 0 006 6h0a6 6 0 006-6V8zm-1.5 6.255a4.5 4.5 0 01-9 0V8a4.5 4.5 0 019 0v6.255z" />
    </svg>
  );
}

function SunIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v2.25m6.364.386l-1.593 1.593M21 12h-2.25M17.25 17.25l-1.593-1.593M12 18.75V21m-6.364-.386l1.593-1.593M3 12h2.25M6.75 6.75L8.343 8.343" />
    </svg>
  );
}

function MoonIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z" />
    </svg>
  );
}

function ChevronDownIcon({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
    </svg>
  );
}

function UserIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.762 0-5.383-.599-7.5-1.632z" />
    </svg>
  );
}

interface NavbarProps {
  onSearchChange?: (query: string) => void;
}

export function Navbar({ onSearchChange }: NavbarProps) {
  const { user, logout, isAuthenticated } = useAuth();
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [scrolled, setScrolled] = useState(false);
  const [mounted, setMounted] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  useEffect(() => {
    if (searchOpen && searchRef.current) {
      searchRef.current.focus();
    }
  }, [searchOpen]);

  const handleSearch = (value: string) => {
    setSearchQuery(value);
    onSearchChange?.(value);
  };

  const handleLogout = async () => {
    await logout();
    router.push('/login');
  };

  const navLinks = [
    { href: '/browse', label: 'Browse' },
    { href: '/my-list', label: 'My List' },
  ];

  return (
    <header
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-500 ${
        scrolled
          ? 'bg-dark-950/95 backdrop-blur-md shadow-lg shadow-black/20'
          : 'bg-gradient-to-b from-black/80 to-transparent'
      }`}
    >
      <nav className="max-w-[1440px] mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        {/* Left: Brand + Nav Links */}
        <div className="flex items-center gap-8">
          <Link href="/browse" className="text-xl sm:text-2xl font-bold tracking-wider text-red-600 hover:text-red-500 transition-colors">
            WILDFRAME
          </Link>
          {isAuthenticated && (
            <div className="hidden md:flex items-center gap-6">
              {navLinks.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  className="text-sm text-gray-300 hover:text-white transition-colors duration-200"
                >
                  {link.label}
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Right: Search, Theme, Notifications, Avatar */}
        <div className="flex items-center gap-3">
          {/* Search Toggle */}
          {isAuthenticated && (
            <div className="relative">
              {searchOpen ? (
                <div className="flex items-center bg-dark-800 border border-dark-600 rounded-md overflow-hidden animate-fade-in">
                  <SearchIcon className="w-4 h-4 text-gray-400 ml-3" />
                  <input
                    ref={searchRef}
                    type="text"
                    value={searchQuery}
                    onChange={(e) => handleSearch(e.target.value)}
                    placeholder="Titles, people, genres"
                    className="bg-transparent text-white placeholder-gray-500 px-3 py-1.5 text-sm w-48 sm:w-64 outline-none"
                    onKeyDown={(e) => {
                      if (e.key === 'Escape') {
                        setSearchOpen(false);
                        handleSearch('');
                      }
                    }}
                  />
                  <button
                    onClick={() => {
                      setSearchOpen(false);
                      handleSearch('');
                    }}
                    className="px-2 text-gray-400 hover:text-white transition-colors"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setSearchOpen(true)}
                  className="p-2 text-gray-300 hover:text-white transition-colors"
                  aria-label="Search"
                >
                  <SearchIcon />
                </button>
              )}
            </div>
          )}

          {/* Theme Toggle */}
          {mounted && (
            <button
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              className="p-2 text-gray-300 hover:text-white transition-colors"
              aria-label="Toggle theme"
            >
              {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
            </button>
          )}

          {/* Notifications */}
          {isAuthenticated && (
            <button className="p-2 text-gray-300 hover:text-white transition-colors relative" aria-label="Notifications">
              <BellIcon />
              <span className="absolute top-1 right-1 w-2 h-2 bg-red-600 rounded-full" />
            </button>
          )}

          {/* Avatar / Profile Dropdown */}
          {isAuthenticated ? (
            <DropdownMenu.Root>
              <DropdownMenu.Trigger asChild>
                <button className="flex items-center gap-2 p-1 rounded-md hover:bg-dark-800 transition-colors group">
                  <div className="w-8 h-8 rounded-md bg-gradient-to-br from-red-500 to-red-700 flex items-center justify-center text-white text-sm font-semibold">
                    {user?.firstName?.[0]?.toUpperCase() || <UserIcon className="w-4 h-4" />}
                  </div>
                  <ChevronDownIcon className="w-3 h-3 text-gray-400 group-hover:text-white transition-transform group-data-[state=open]:rotate-180" />
                </button>
              </DropdownMenu.Trigger>

              <DropdownMenu.Portal>
                <DropdownMenu.Content
                  className="min-w-[200px] bg-dark-800 border border-dark-700 rounded-lg p-1.5 shadow-xl shadow-black/40 animate-scale-in z-50"
                  sideOffset={8}
                  align="end"
                >
                  <div className="px-3 py-2 border-b border-dark-700 mb-1">
                    <p className="text-sm font-medium text-white">{user?.firstName} {user?.lastName}</p>
                    <p className="text-xs text-gray-400">{user?.email}</p>
                  </div>

                  <DropdownMenu.Item asChild>
                    <Link href="/account" className="flex items-center gap-3 px-3 py-2 text-sm text-gray-300 hover:text-white hover:bg-dark-700 rounded-md cursor-pointer outline-none transition-colors">
                      <UserIcon className="w-4 h-4" /> Account
                    </Link>
                  </DropdownMenu.Item>
                  <DropdownMenu.Item asChild>
                    <Link href="/billing" className="flex items-center gap-3 px-3 py-2 text-sm text-gray-300 hover:text-white hover:bg-dark-700 rounded-md cursor-pointer outline-none transition-colors">
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 8.25h19.5M2.25 9h19.5m-16.5 5.25h6m-6 2.25h3m-3.75 3H18a2.25 2.25 0 002.25-2.25V6.75A2.25 2.25 0 0018 4.5H6a2.25 2.25 0 00-2.25 2.25v10.5A2.25 2.25 0 006 19.5h1.5" />
                      </svg>
                      Billing
                    </Link>
                  </DropdownMenu.Item>

                  <DropdownMenu.Separator className="h-px bg-dark-700 my-1" />

                  <DropdownMenu.Item
                    onSelect={handleLogout}
                    className="flex items-center gap-3 px-3 py-2 text-sm text-red-400 hover:text-red-300 hover:bg-dark-700 rounded-md cursor-pointer outline-none transition-colors"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9" />
                    </svg>
                    Sign Out
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          ) : (
            <div className="flex items-center gap-3">
              <Link
                href="/login"
                className="text-sm text-gray-300 hover:text-white transition-colors"
              >
                Sign In
              </Link>
              <Link
                href="/signup"
                className="bg-red-600 hover:bg-red-700 text-white text-sm px-4 py-1.5 rounded-md font-medium transition-colors"
              >
                Sign Up
              </Link>
            </div>
          )}
        </div>
      </nav>
    </header>
  );
}
