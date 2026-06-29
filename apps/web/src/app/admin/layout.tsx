/**
 * Admin layout — sidebar navigation + top bar.
 * Gated: non-admin users are redirected to /login (see AdminGate).
 */
'use client';

import { ReactNode, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import clsx from 'clsx';
import { Toaster } from 'sonner';
import { useAuth, useIsAdmin, useUser } from '@/hooks';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import {
  DashboardIcon,
  UsersIcon,
  FlagIcon,
  BellIcon,
  SettingsIcon,
  ClipboardIcon,
  MenuIcon,
  LogoutIcon,
} from '@/components/admin/icons';

const NAV = [
  { href: '/admin', label: 'Dashboard', icon: DashboardIcon },
  { href: '/admin/users', label: 'Users', icon: UsersIcon },
  { href: '/admin/flags', label: 'Content Flags', icon: FlagIcon },
  { href: '/admin/alerts', label: 'Alerts', icon: BellIcon },
  { href: '/admin/config', label: 'System Config', icon: SettingsIcon },
  { href: '/admin/audit', label: 'Audit Log', icon: ClipboardIcon },
];

function AdminGate({ children }: { children: ReactNode }) {
  const isAuthenticated = useAuth();
  const isAdmin = useIsAdmin();
  const router = useRouter();
  const authed = isAuthenticated.isAuthenticated;

  useEffect(() => {
    if (!authed) {
      router.replace('/login');
      return;
    }
    if (authed && !isAdmin) {
      router.replace('/account');
    }
  }, [authed, isAdmin, router]);

  if (!authed) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-black text-zinc-400">
        <div className="flex items-center gap-3">
          <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
          Redirecting to sign in…
        </div>
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-black text-zinc-400">
        <div className="flex items-center gap-3">
          <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />
          Checking permissions…
        </div>
      </div>
    );
  }

  return <>{children}</>;
}

export default function AdminLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const user = useUser();
  const { logout } = useAuth();
  const router = useRouter();
  const [navOpen, setNavOpen] = useState(false);

  const handleLogout = async () => {
    await logout();
    router.push('/login');
  };

  return (
    <AdminGate>
      <div className="min-h-screen bg-black text-zinc-100">
        {/* Mobile top bar */}
        <header className="sticky top-0 z-30 flex items-center justify-between border-b border-white/10 bg-zinc-950/90 px-4 py-3 backdrop-blur lg:hidden">
          <button
            onClick={() => setNavOpen((v) => !v)}
            aria-label="Toggle navigation"
            className="rounded-lg p-2 text-zinc-300 hover:bg-white/5"
          >
            <MenuIcon />
          </button>
          <Link href="/admin" className="text-lg font-bold tracking-tight text-red-500">
            WILDFRAME
          </Link>
          <div className="h-9 w-9 rounded-full bg-red-600/20 text-red-400 grid place-items-center text-xs font-bold uppercase">
            {user?.firstName?.[0] ?? 'A'}
          </div>
        </header>

        <div className="flex">
          {/* Sidebar */}
          <aside
            className={clsx(
              'fixed inset-y-0 left-0 z-40 w-64 transform border-r border-white/10 bg-zinc-950 transition-transform lg:static lg:translate-x-0',
              navOpen ? 'translate-x-0' : '-translate-x-full',
            )}
          >
            <div className="flex h-full flex-col">
              <div className="flex h-16 items-center gap-2 border-b border-white/10 px-5">
                <div className="h-8 w-8 grid place-items-center rounded-lg bg-red-600 text-sm font-black text-white">
                  W
                </div>
                <div>
                  <p className="text-sm font-bold leading-none text-white">Wildframe</p>
                  <p className="text-[11px] text-zinc-500">Admin Console</p>
                </div>
              </div>

              <nav className="flex-1 overflow-y-auto px-3 py-4">
                <p className="px-2 pb-2 text-[10px] font-semibold uppercase tracking-widest text-zinc-600">
                  Manage
                </p>
                <ul className="space-y-1">
                  {NAV.map((item) => {
                    const active =
                      item.href === '/admin'
                        ? pathname === '/admin'
                        : pathname.startsWith(item.href);
                    const Icon = item.icon;
                    return (
                      <li key={item.href}>
                        <Link
                          href={item.href}
                          onClick={() => setNavOpen(false)}
                          className={clsx(
                            'group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition',
                            active
                              ? 'bg-red-600/15 text-red-400'
                              : 'text-zinc-400 hover:bg-white/5 hover:text-white',
                          )}
                        >
                          <Icon
                            className={clsx(
                              'shrink-0',
                              active ? 'text-red-400' : 'text-zinc-500 group-hover:text-zinc-300',
                            )}
                          />
                          {item.label}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </nav>

              <div className="border-t border-white/10 p-3">
                <div className="mb-2 flex items-center gap-3 rounded-lg px-2 py-2">
                  <div className="h-9 w-9 grid place-items-center rounded-full bg-red-600/20 text-xs font-bold uppercase text-red-400">
                    {user?.firstName?.[0] ?? 'A'}
                    {user?.lastName?.[0] ?? ''}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-white">
                      {user?.firstName} {user?.lastName}
                    </p>
                    <p className="truncate text-xs text-zinc-500">{user?.email}</p>
                  </div>
                </div>
                <button
                  onClick={handleLogout}
                  className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-zinc-400 transition hover:bg-white/5 hover:text-white"
                >
                  <LogoutIcon className="shrink-0 text-zinc-500" />
                  Sign out
                </button>
              </div>
            </div>
          </aside>

          {/* Backdrop for mobile */}
          {navOpen && (
            <div
              className="fixed inset-0 z-30 bg-black/60 lg:hidden"
              onClick={() => setNavOpen(false)}
            />
          )}

          {/* Main content */}
          <main className="flex-1">
            <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{children}</div>
          </main>
        </div>
      </div>
      <Toaster
        theme="dark"
        position="top-right"
        toastOptions={{
          style: {
            background: '#18181b',
            color: '#fff',
            border: 'rgba(255,255,255,0.1)',
          },
        }}
      />
    </AdminGate>
  );
}
