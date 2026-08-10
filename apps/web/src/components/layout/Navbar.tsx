'use client';

import Link from 'next/link';
import { useAuth } from '@/hooks';
import { useRouter, usePathname } from 'next/navigation';
import { useState, useRef, useEffect } from 'react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';

// Search icon keeps the navigation independent from an icon package.
function SearchIcon({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
    </svg>
  );
}

// Notification icon is intentionally small so it does not dominate the header.
function BellIcon({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.15a2 2 0 01-2.828 0M18 8V6a6 6 0 00-12 0v2a6 6 0 006 6h0a6 6 0 006-6V8zm-1.5 6.255a4.5 4.5 0 01-9 0V8a4.5 4.5 0 019 0v6.255z" />
    </svg>
  );
}

// Chevron indicates that the profile control opens a menu.
function ChevronDownIcon({ className = 'w-3 h-3' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
    </svg>
  );
}

// User icon is the fallback when an account has no first-name initial.
function UserIcon({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.762 0-5.383-.599-7.5-1.632z" />
    </svg>
  );
}

interface NavbarProps {
  onSearchChange?: (query: string) => void;
}

const NAV_LINKS = [
  { href: '/browse', label: 'Home' },
  { href: '/my-list', label: 'My List' },
];

export function Navbar({ onSearchChange }: NavbarProps) {
  const { user, logout, isAuthenticated } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [scrolled, setScrolled] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const searchWrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Only promote the navbar after a small scroll so the hero remains visually open.
    const handleScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  useEffect(() => {
    // Focus the search field immediately after its animation mounts.
    if (searchOpen && searchRef.current) searchRef.current.focus();
  }, [searchOpen]);

  useEffect(() => {
    // Close the search popover when the user clicks outside it.
    const onClickOutside = (e: MouseEvent) => {
      if (searchWrapRef.current && !searchWrapRef.current.contains(e.target as Node)) {
        setSearchOpen(false);
      }
    };
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, []);

  const handleSearch = (value: string) => {
    setSearchQuery(value);
    onSearchChange?.(value);
  };

  const handleLogout = async () => {
    await logout();
    router.push('/login');
  };

  return (
    <header
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled || searchOpen
          ? 'wf-glass shadow-[0_12px_40px_rgba(0,0,0,0.22)]'
          : 'bg-gradient-to-b from-black/80 via-black/40 to-transparent'
      }`}
    >
      <nav className="mx-auto flex h-[68px] max-w-[1800px] items-center justify-between px-4 sm:px-6 lg:px-8">
        {/* Brand and navigation stay compact on smaller screens. */}
        <div className="flex min-w-0 items-end gap-5 sm:gap-7">
          <Link
            href="/browse"
            className="select-none text-[22px] font-black tracking-[-0.04em] text-[#E50914] transition-transform duration-200 hover:scale-[1.03] sm:text-[26px]"
          >
            WILDFRAME
          </Link>
          {isAuthenticated && (
            <div className="hidden items-center gap-5 md:flex">
              {NAV_LINKS.map((link) => {
                const isActive =
                  link.href === '/browse'
                    ? pathname === '/browse' || pathname === '/'
                    : pathname.startsWith(link.href);
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    className={`relative py-6 text-sm transition-colors duration-200 ${
                      isActive ? 'font-semibold text-white' : 'text-gray-300 hover:text-white'
                    }`}
                  >
                    {link.label}
                    {/* Active indicator gives navigation state without relying on color alone. */}
                    <span
                      className={`absolute bottom-2 left-0 h-0.5 rounded-full bg-[#E50914] transition-all duration-300 ${
                        isActive ? 'w-full' : 'w-0'
                      }`}
                    />
                  </Link>
                );
              })}
            </div>
          )}
        </div>

        {/* Search, notification, and profile controls. */}
        <div className="flex items-center gap-3 sm:gap-5">
          {isAuthenticated && (
            <div ref={searchWrapRef} className="relative flex items-center">
              {searchOpen && (
                <div className="animate-scale-in absolute right-9 top-1/2 flex -translate-y-1/2 items-center border border-white/20 bg-[#111] shadow-2xl">
                  <SearchIcon className="ml-3 h-3.5 w-3.5 text-gray-400" />
                  <input
                    ref={searchRef}
                    type="text"
                    value={searchQuery}
                    onChange={(e) => handleSearch(e.target.value)}
                    placeholder="Titles, people, genres"
                    aria-label="Search titles, people, or genres"
                    className="w-44 bg-transparent px-3 py-2.5 text-[13px] text-white outline-none placeholder:text-gray-500 sm:w-60"
                    onKeyDown={(e) => {
                      if (e.key === 'Escape') {
                        setSearchOpen(false);
                        handleSearch('');
                      }
                    }}
                  />
                </div>
              )}
              <button
                type="button"
                onClick={() => setSearchOpen((v) => !v)}
                className="rounded-full p-1.5 text-white transition-all duration-200 hover:bg-white/10 hover:scale-110"
                aria-label={searchOpen ? 'Close search' : 'Search'}
              >
                <SearchIcon className="h-5 w-5" />
              </button>
            </div>
          )}

          {isAuthenticated && (
            <button
              type="button"
              className="relative rounded-full p-1.5 text-white transition-all duration-200 hover:bg-white/10 hover:scale-110"
              aria-label="Notifications"
            >
              <BellIcon className="h-5 w-5" />
              {/* Notification dot is decorative until the notifications API is wired. */}
              <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-[#E50914]" />
            </button>
          )}

          {isAuthenticated ? (
            <DropdownMenu.Root>
              <DropdownMenu.Trigger asChild>
                <button
                  type="button"
                  className="group flex items-center gap-1.5 rounded-md p-0.5 text-white transition-all duration-200 hover:bg-white/10"
                  aria-label="Profile menu"
                >
                  <div className="flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br from-[#e50914] to-[#70050a] text-xs font-bold text-white shadow-lg shadow-black/30 transition-transform duration-200 group-hover:scale-105">
                    {user?.firstName?.[0]?.toUpperCase() || <UserIcon className="h-3.5 w-3.5" />}
                  </div>
                  <ChevronDownIcon className="h-3 w-3 text-gray-300 transition-transform duration-200 group-data-[state=open]:rotate-180" />
                </button>
              </DropdownMenu.Trigger>

              <DropdownMenu.Portal>
                <DropdownMenu.Content
                  className="animate-scale-in z-50 min-w-[220px] border border-white/10 bg-[#141414]/95 p-1.5 shadow-2xl backdrop-blur-xl"
                  sideOffset={10}
                  align="end"
                >
                  <div className="mb-1 border-b border-white/10 px-3 py-2.5">
                    <p className="text-sm font-medium text-white">{user?.firstName} {user?.lastName}</p>
                    <p className="truncate text-xs text-gray-400">{user?.email}</p>
                  </div>

                  <DropdownMenu.Item asChild>
                    <Link href="/account" className="flex cursor-pointer items-center gap-3 rounded px-3 py-2.5 text-[13px] text-gray-300 outline-none transition-colors hover:bg-white/10 hover:text-white">
                      <UserIcon className="h-4 w-4" /> Account
                    </Link>
                  </DropdownMenu.Item>
                  <DropdownMenu.Item asChild>
                    <Link href="/billing" className="flex cursor-pointer items-center gap-3 rounded px-3 py-2.5 text-[13px] text-gray-300 outline-none transition-colors hover:bg-white/10 hover:text-white">
                      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 8.25h19.5M2.25 9h19.5m-16.5 5.25h6m-6 2.25h3m-3.75 3H18a2.25 2.25 0 002.25-2.25V6.75A2.25 2.25 0 0018 4.5H6a2.25 2.25 0 00-2.25 2.25v10.5A2.25 2.25 0 006 19.5h1.5" />
                      </svg>
                      Billing
                    </Link>
                  </DropdownMenu.Item>

                  <DropdownMenu.Separator className="my-1 h-px bg-white/10" />

                  <DropdownMenu.Item
                    onSelect={handleLogout}
                    className="flex cursor-pointer items-center gap-3 rounded px-3 py-2.5 text-sm text-gray-300 outline-none transition-colors hover:bg-white/10 hover:text-white"
                  >
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9" />
                    </svg>
                    Sign Out of Wildframe
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          ) : (
            <div className="flex items-center gap-2 sm:gap-3">
              <Link href="/login" className="rounded px-2 py-1.5 text-sm text-gray-300 transition-colors hover:text-white">
                Sign In
              </Link>
              <Link
                href="/signup"
                className="rounded bg-[#E50914] px-3.5 py-1.5 text-sm font-semibold text-white shadow-lg shadow-red-950/30 transition-all duration-200 hover:-translate-y-0.5 hover:bg-[#f6121d] hover:shadow-red-950/50"
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
