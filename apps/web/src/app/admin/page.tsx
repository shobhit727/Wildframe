/**
 * Admin dashboard overview — KPI cards + recent activity + chart placeholders.
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

  // Recent users feeds the "recent activity" panel.
  const recentUsers = useUsers({ limit: 6 });

  const s = stats.data;

  return (
    <div className="space-y-8">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white sm:text-3xl">Dashboard</h1>
          <p className="mt-1 text-sm text-zinc-400">
            System overview and key metrics for your Wildframe platform.
          </p>
        </div>
        <AdminButton
          variant="secondary"
          size="sm"
          onClick={() => stats.refetch()}
          disabled={stats.isFetching}
        >
          <Icons.RefreshIcon className={stats.isFetching ? 'animate-spin' : ''} />
          {stats.isFetching ? 'Refreshing' : 'Refresh'}
        </AdminButton>
      </div>

      {/* KPI cards */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.isLoading || !s ? (
          <>
            <StatCardSkeleton />
            <StatCardSkeleton />
            <StatCardSkeleton />
            <StatCardSkeleton />
          </>
        ) : (
          <>
            <StatCard
              label="Total Users"
              value={formatNumber(s.total_users)}
              hint={`${formatNumber(s.active_users)} active`}
              trend={{ value: 4.5, positive: true }}
              icon={<Icons.UsersIcon />}
              accent="sky"
            />
            <StatCard
              label="Active Streams"
              value={formatNumber(Math.round(s.active_users * 0.32))}
              hint="last 24h"
              trend={{ value: 2.1, positive: true }}
              icon={<Icons.ActivityIcon />}
              accent="green"
            />
            <StatCard
              label="Open Flags"
              value={formatNumber(s.flagged_content)}
              hint={`${s.active_alerts} alerts`}
              trend={{ value: 1.4, positive: false }}
              icon={<Icons.FlagIcon />}
              accent="amber"
            />
            <StatCard
              label="MRR"
              value={`$${formatNumber(48250)}`}
              hint={`uptime ${formatUptime(s.system_uptime_hours)}`}
              trend={{ value: 6.8, positive: true }}
              icon={<Icons.DollarIcon />}
              accent="purple"
            />
          </>
        )}
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Chart placeholder */}
        <section className="lg:col-span-2 rounded-xl border border-white/10 bg-zinc-900/40 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-base font-semibold text-white">Traffic (last 14 days)</h2>
            <span className="text-xs text-zinc-500">placeholder</span>
          </div>
          <ChartPlaceholder />
        </section>

        {/* Recent activity */}
        <section className="rounded-xl border border-white/10 bg-zinc-900/40 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-base font-semibold text-white">Recent Users</h2>
            <a href="/admin/users" className="text-xs text-red-400 hover:text-red-300">
              View all →
            </a>
          </div>
          {recentUsers.isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="h-10 animate-pulse rounded-lg bg-zinc-800/60" />
              ))}
            </div>
          ) : recentUsers.data && recentUsers.data.length > 0 ? (
            <ul className="space-y-2">
              {(recentUsers.data as AdminUser[]).map((u) => (
                <li
                  key={u.user_id}
                  className="flex items-center gap-3 rounded-lg px-2 py-2 transition hover:bg-white/[0.03]"
                >
                  <div className="h-8 w-8 grid place-items-center rounded-full bg-red-600/15 text-xs font-bold uppercase text-red-400">
                    {(u.first_name?.[0] ?? 'U').toUpperCase()}
                    {(u.last_name?.[0] ?? '').toUpperCase()}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-white">
                      {u.first_name} {u.last_name}
                    </p>
                    <p className="truncate text-xs text-zinc-500">{u.email}</p>
                  </div>
                  <span className="text-[11px] text-zinc-500">
                    {u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              icon={<Icons.UsersIcon />}
              title="No users yet"
              description="User accounts will appear here as they register."
            />
          )}
        </section>
      </div>
    </div>
  );
}

function ChartPlaceholder() {
  // Decorative SVG bar chart placeholder — no charting dependency.
  const bars = [40, 65, 50, 80, 55, 90, 70, 85, 60, 95, 75, 88, 68, 92];
  const max = 100;
  return (
    <div className="flex h-56 items-end gap-1.5 sm:gap-2.5">
      {bars.map((h, i) => (
        <div
          key={i}
          className="flex-1 rounded-t bg-gradient-to-t from-red-600/40 to-red-500/10 transition-all duration-500 hover:from-red-500/60 hover:to-red-400/20"
          style={{ height: `${(h / max) * 100}%` }}
          aria-hidden
        />
      ))}
    </div>
  );
}
