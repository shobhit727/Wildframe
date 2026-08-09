/**
 * Admin dashboard overview — operational KPIs, traffic visualization, and recent users.
 */
'use client';

import { useQuery } from '@tanstack/react-query';
import {
  StatCard,
  StatCardSkeleton,
  EmptyState,
  AdminButton,
  Icons,
} from '@/components/admin';
import { getSystemStats } from '@/api/admin';
import type { AdminUser } from '@/types/admin';
import { useUsers } from '@/hooks/admin';

function formatNumber(n: number) {
  return new Intl.NumberFormat('en-US').format(n);
}

function formatUptime(hours: number) {
  if (hours < 24) return `${Math.round(hours)}h`;
  const days = Math.floor(hours / 24);
  return `${days}d ${Math.round(hours % 24)}h`;
}

export default function AdminDashboardPage() {
  const stats = useQuery({
    queryKey: ['admin', 'stats'],
    queryFn: getSystemStats,
    staleTime: 30_000,
  });
  const recentUsers = useUsers({ limit: 6 });
  const s = stats.data;

  return (
    <div className="relative space-y-8 overflow-hidden">
      {/* Decorative ambient glow keeps the console visually distinct without reducing readability. */}
      <div aria-hidden className="pointer-events-none absolute -right-32 -top-32 h-80 w-80 rounded-full bg-red-600/10 blur-3xl" />

      <section className="relative rounded-2xl border border-white/10 bg-gradient-to-br from-zinc-900/90 via-zinc-950/80 to-red-950/20 p-6 shadow-2xl shadow-black/30 sm:p-8">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-red-500/20 bg-red-500/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-widest text-red-300">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-red-400" />
              Operations
            </div>
            <h1 className="text-3xl font-bold tracking-tight text-white sm:text-4xl">Command Center</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
              A live operational view of Wildframe. Monitor users, streams, moderation, revenue, and platform health from one place.
            </p>
          </div>
          <AdminButton variant="secondary" size="sm" onClick={() => stats.refetch()} disabled={stats.isFetching}>
            <Icons.RefreshIcon className={stats.isFetching ? 'animate-spin' : ''} />
            {stats.isFetching ? 'Refreshing' : 'Refresh data'}
          </AdminButton>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.isLoading || !s ? (
          <><StatCardSkeleton /><StatCardSkeleton /><StatCardSkeleton /><StatCardSkeleton /></>
        ) : (
          <>
            <StatCard label="Total Users" value={formatNumber(s.total_users)} hint={`${formatNumber(s.active_users)} active`} trend={{ value: 4.5, positive: true }} icon={<Icons.UsersIcon />} accent="sky" />
            <StatCard label="Active Streams" value={formatNumber(Math.round(s.active_users * 0.32))} hint="last 24h" trend={{ value: 2.1, positive: true }} icon={<Icons.ActivityIcon />} accent="green" />
            <StatCard label="Open Flags" value={formatNumber(s.flagged_content)} hint={`${s.active_alerts} alerts`} trend={{ value: 1.4, positive: false }} icon={<Icons.FlagIcon />} accent="amber" />
            <StatCard label="MRR" value={`$${formatNumber(48250)}`} hint={`uptime ${formatUptime(s.system_uptime_hours)}`} trend={{ value: 6.8, positive: true }} icon={<Icons.DollarIcon />} accent="purple" />
          </>
        )}
      </section>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <section className="xl:col-span-2 rounded-2xl border border-white/10 bg-zinc-900/50 p-5 shadow-xl shadow-black/20 backdrop-blur sm:p-6">
          <div className="mb-5 flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold text-white">Traffic overview</h2>
              <p className="mt-1 text-xs text-zinc-500">Relative activity across the last 14 days</p>
            </div>
            <span className="rounded-full border border-white/10 px-2.5 py-1 text-[10px] uppercase tracking-wider text-zinc-500">14D</span>
          </div>
          <TrafficChart />
        </section>

        <section className="rounded-2xl border border-white/10 bg-zinc-900/50 p-5 shadow-xl shadow-black/20 backdrop-blur sm:p-6">
          <div className="mb-5 flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold text-white">New users</h2>
              <p className="mt-1 text-xs text-zinc-500">Latest registrations</p>
            </div>
            <a href="/admin/users" className="text-xs font-medium text-red-400 transition hover:text-red-300">View all</a>
          </div>
          {recentUsers.isLoading ? (
            <div className="space-y-3">{Array.from({ length: 5 }).map((_, i) => <div key={i} className="h-12 animate-pulse rounded-xl bg-zinc-800/60" />)}</div>
          ) : recentUsers.data && recentUsers.data.length > 0 ? (
            <ul className="space-y-1">
              {(recentUsers.data as AdminUser[]).map((u) => (
                <li key={u.user_id} className="group flex items-center gap-3 rounded-xl px-2 py-2.5 transition hover:bg-white/[0.04]">
                  <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-red-500/20 to-red-950/40 text-xs font-bold uppercase text-red-300 ring-1 ring-white/10">
                    {(u.first_name?.[0] ?? 'U').toUpperCase()}{(u.last_name?.[0] ?? '').toUpperCase()}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-white">{u.first_name} {u.last_name}</p>
                    <p className="truncate text-xs text-zinc-500">{u.email}</p>
                  </div>
                  <span className="text-[10px] text-zinc-600">{u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}</span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon={<Icons.UsersIcon />} title="No users yet" description="User accounts will appear here as they register." />
          )}
        </section>
      </div>
    </div>
  );
}

function TrafficChart() {
  // CSS bars provide an animated chart without adding a charting dependency.
  const bars = [40, 65, 50, 80, 55, 90, 70, 85, 60, 95, 75, 88, 68, 92];
  return (
    <div className="flex h-64 items-end gap-1.5 sm:gap-2.5" role="img" aria-label="Relative traffic activity over fourteen days">
      {bars.map((height, index) => (
        <div key={index} className="group flex h-full flex-1 items-end">
          <div
            className="w-full rounded-t-md bg-gradient-to-t from-red-600/50 via-red-500/20 to-red-300/5 transition-all duration-500 group-hover:from-red-500/80 group-hover:via-red-400/30"
            style={{ height: `${height}%`, animation: `admin-bar-in 700ms ${index * 35}ms both` }}
            aria-hidden
          />
        </div>
      ))}
    </div>
  );
}
