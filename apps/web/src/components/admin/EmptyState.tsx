/**
 * EmptyState — shown when a table/list has no rows.
 */
import { ReactNode } from 'react';

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-white/10 bg-zinc-900/40 px-6 py-16 text-center">
      {icon && <div className="mb-4 text-zinc-500">{icon}</div>}
      <h3 className="text-base font-semibold text-white">{title}</h3>
      {description && <p className="mt-1 max-w-sm text-sm text-zinc-400">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
