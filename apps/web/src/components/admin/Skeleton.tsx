/**
 * Skeleton + shimmer loaders for admin async states.
 */
import clsx from 'clsx';

export function Skeleton({
  className,
  style,
}: {
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      className={clsx(
        'animate-pulse rounded-md bg-gradient-to-r from-zinc-800 via-zinc-700/60 to-zinc-800 bg-[length:1000px_100%]',
        className,
      )}
      style={style}
    />
  );
}

export function StatCardSkeleton() {
  return (
    <div className="rounded-xl border border-white/10 bg-zinc-900/60 p-5">
      <Skeleton className="h-3 w-24" />
      <Skeleton className="mt-3 h-8 w-32" />
      <Skeleton className="mt-3 h-3 w-20" />
    </div>
  );
}

export function TableRowSkeleton({ cols = 5, rows = 6 }: { cols?: number; rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="grid gap-3" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
          {Array.from({ length: cols }).map((__, c) => (
            <Skeleton key={c} className="h-4 w-full" />
          ))}
        </div>
      ))}
    </div>
  );
}
