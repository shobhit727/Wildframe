/**
 * StatCard — KPI tile used on the dashboard top bar.
 * Shows a label, a large value, an optional trend, and an icon slot.
 */
import clsx from 'clsx';
import { ReactNode } from 'react';

interface StatCardProps {
  label: string;
  value: ReactNode;
  hint?: string;
  trend?: { value: number; positive?: boolean };
  icon?: ReactNode;
  accent?: 'red' | 'sky' | 'green' | 'amber' | 'purple';
}

const ACCENT: Record<NonNullable<StatCardProps['accent']>, string> = {
  red: 'from-red-500/20 to-red-500/0 text-red-400',
  sky: 'from-sky-500/20 to-sky-500/0 text-sky-400',
  green: 'from-green-500/20 to-green-500/0 text-green-400',
  amber: 'from-amber-500/20 to-amber-500/0 text-amber-400',
  purple: 'from-purple-500/20 to-purple-500/0 text-purple-400',
};

export function StatCard({
  label,
  value,
  hint,
  trend,
  icon,
  accent = 'sky',
}: StatCardProps) {
  return (
    <div className="relative overflow-hidden rounded-xl border border-white/10 bg-zinc-900/60 p-5 backdrop-blur transition hover:border-white/20 hover:bg-zinc-900/80">
      <div className={clsx('pointer-events-none absolute -right-6 -top-6 h-24 w-24 rounded-full bg-gradient-to-br opacity-40', ACCENT[accent])} />
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-zinc-400">{label}</p>
          <p className="mt-2 text-3xl font-bold tabular-nums text-white">{value}</p>
        </div>
        {icon && (
          <div className={clsx('rounded-lg bg-white/5 p-2', ACCENT[accent])}>{icon}</div>
        )}
      </div>
      <div className="mt-3 flex items-center gap-2 text-xs">
        {trend && (
          <span
            className={clsx(
              'inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium',
              trend.positive ? 'bg-green-500/15 text-green-400' : 'bg-red-500/15 text-red-400',
            )}
          >
            {trend.positive ? '↑' : '↓'} {Math.abs(trend.value)}%
          </span>
        )}
        {hint && <span className="text-zinc-500">{hint}</span>}
      </div>
    </div>
  );
}
