'use client';

import Link from 'next/link';
import { useAuth } from '@/hooks';
import { useRouter, usePathname } from 'next/navigation';
import { useState, useRef, useEffect } from 'react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';

function SearchIcon({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
    </svg>
  );
}

function BellIcon({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.15a2 2 0 01-2.828 0M18 8V6a6 6 0 00-12 0v2a6 6 0 006 6h0a6 6 0 006-6V8zm-1.5 6.255a4.5 4.5 0 01-9 0V8a4.5 4.5 0 019 0v6.255z" />
    </svg>
  );
}

function ChevronDownIcon({ className = 'w-3 h-3' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
    </svg>
  );
}

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
    const handleScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  useEffect(() => {
    if (searchOpen && searchRef.current) searchRef.current.focus();
  }, [searchOpen]);

  useEffect(() => {
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
      className={`fixed top-0 left-0 right-0 z-50 transition-colors duration-300 ${
        scrolled || searchOpen ? 'bg-[#141414]' : 'bg-gradient-to-b from-black/70 via-black/40 to-transparent'
      }`}
    >
      <nav className="px-8 h-[68px] flex items-center justify-between">
        {/* Left: Brand + Nav Links */}
        <div className="flex items-end gap-6">
          <Link href="/browse" className="text-[26px] font-semibold tracking-tight text-[#E50914] leading-none select-none">
            WILDFRAME
          </Link>
          {isAuthenticated && (
            <div className="hidden md:flex items-center gap-5">
              {NAV_LINKS.map((link) => {
                const isActive =
                  link.href === '/browse'
                    ? pathname === '/browse' || pathname === '/'
                    : pathname.startsWith(link.href);
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    className={`text-sm transition-colors duration-200 ${
                      isActive ? 'text-white font-semibold' : 'text-gray-300 hover:text-white'
                    }`}
                  >
                    {link.label}
                  </Link>
                );
              })}
            </div>
          )}
        </div>

        {/* Right: Search, Profile */}
        <div className="flex items-center gap-5">
          {/* Search */}
          {isAuthenticated && (
            <div ref={searchWrapRef} className="relative flex items-center gap-2">
              {searchOpen && (
                <div className="absolute right-8 -bottom-1.5 flex items-center bg-[#141414] border border-white/30">
                  <SearchIcon className="w-3.5 h-3.5 text-gray-400 ml-3" />
                  <input
                    ref={searchRef}
                    type="text"
                    value={searchQuery}
                    onChange={(e) => handleSearch(e.target.value)}
                    placeholder="Titles, people, genres"
                    className="bg-transparent text-[13px] text-white placeholder-gray-400 px-3 py-2 w-48 sm:w-60 outline-none"
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
                onClick={() => setSearchOpen((v) => !v)}
                className="text-white transition-transform hover:scale-110"
                aria-label="Search"
              >
                <SearchIcon className="w-5 h-5" />
              </button>
            </div>
          )}

          {isAuthenticated && (
            <button className="text-white relative" aria-label="Notifications">
              <BellIcon className="w-5 h-5" />
              <span className="absolute top-0 right-0 w-1.5 h-1.5 bg-[#E50914] rounded-full" />
            </button>
          )}

          {/* Profile Dropdown */}
          {isAuthenticated ? (
            <DropdownMenu.Root>
              <DropdownMenu.Trigger asChild>
                <button className="flex items-center gap-1.5 text-white" aria-label="Profile menu">
                  <div className="w-7 h-7 rounded-md bg-gradient-to-br from-[#E50914] to-[#8f060c] flex items-center justify-center text-white text-xs font-semibold">
                    {user?.firstName?.[0]?.toUpperCase() || <UserIcon className="w-3.5 h-3.5" />}
                  </div>
                  <ChevronDownIcon className="w-3 h-3 transition-transform group-data-[state=open]:rotate-180" />
                </button>
              </DropdownMenu.Trigger>

              <DropdownMenu.Portal>
                <DropdownMenu.Content
                  className="min-w-[200px] bg-[#141414] border border-white/10 shadow-2xl p-1.5 z-50"
                  sideOffset={8}
                  align="end"
                >
                  <div className="px-3 py-2 border-b border-white/10 mb-1">
                    <p className="text-sm font-medium text-white">{user?.firstName} {user?.lastName}</p>
                    <p className="text-xs text-gray-400">{user?.email}</p>
                  </div>

                  <DropdownMenu.Item asChild>
                    <Link href="/account" className="flex items-center gap-3 px-3 py-2.5 text-[13px] text-gray-300 hover:underline cursor-pointer outline-none">
                      <UserIcon className="w-4 h-4" /> Account
                    </Link>
                  </DropdownMenu.Item>
                  <DropdownMenu.Item asChild>
                    <Link href="/billing" className="flex items-center gap-3 px-3 py-2.5 text-[13px] text-gray-300 hover:underline cursor-pointer outline-none">
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 8.25h19.5M2.25 9h19.5m-16.5 5.25h6m-6 2.25h3m-3.75 3H18a2.25 2.25 0 002.25-2.25V6.75A2.25 2.25 0 0018 4.5H6a2.25 2.25 0 00-2.25 2.25v10.5A2.25 2.25 0 006 19.5h1.5" />
                      </svg>
                      Billing
                    </Link>
                  </DropdownMenu.Item>

                  <DropdownMenu.Separator className="h-px bg-white/10 my-1" />

                  <DropdownMenu.Item
                    onSelect={handleLogout}
                    className="flex items-center gap-3 px-3 py-2.5 text-sm text-gray-300 hover:underline cursor-pointer outline-none"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9" />
                    </svg>
                    Sign Out of Wildframe
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          ) : (
            <div className="flex items-center gap-3">
              <Link href="/login" className="text-sm text-gray-300 hover:text-white transition-colors">
                Sign In
              </Link>
              <Link
                href="/signup"
                className="bg-[#E50914] hover:bg-[#F6121D] text-white text-sm px-4 py-1.5 rounded font-medium transition-colors"
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