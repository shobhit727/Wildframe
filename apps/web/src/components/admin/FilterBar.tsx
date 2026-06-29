/**
 * FilterBar — search input + slot for extra controls (status filter, etc).
 */
import clsx from 'clsx';
import { ReactNode } from 'react';
import { SearchIcon } from './icons';

interface FilterBarProps {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  left?: ReactNode;
  right?: ReactNode;
}

export function FilterBar({
  value,
  onChange,
  placeholder = 'Search…',
  left,
  right,
}: FilterBarProps) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex flex-1 items-center gap-3">
        <div className="relative flex-1 sm:max-w-xs">
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
          <input
            type="text"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            className={clsx(
              'w-full rounded-lg border border-white/10 bg-zinc-900/60 py-2 pl-9 pr-3 text-sm text-white',
              'placeholder:text-zinc-500 focus:border-red-500/50 focus:outline-none focus:ring-2 focus:ring-red-500/30',
            )}
          />
        </div>
        {left}
      </div>
      {right}
    </div>
  );
}
