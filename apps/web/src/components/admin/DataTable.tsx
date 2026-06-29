/**
 * DataTable — accessible, sortable, paginated table.
 * Pure presentational: parent owns data + sort/pagination state.
 */
import clsx from 'clsx';
import { ReactNode } from 'react';
import { ChevronDownIcon, ChevronUpIcon } from './icons';

export interface Column<T> {
  key: string;
  header: ReactNode;
  sortable?: boolean;
  className?: string;
  render: (row: T) => ReactNode;
}

export interface SortState {
  key: string;
  dir: 'asc' | 'desc';
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  sort?: SortState;
  onSort?: (key: string) => void;
  empty?: ReactNode;
  density?: 'compact' | 'comfortable';
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  sort,
  onSort,
  empty,
  density = 'comfortable',
}: DataTableProps<T>) {
  const padY = density === 'compact' ? 'py-2.5' : 'py-3.5';

  return (
    <div className="overflow-hidden rounded-xl border border-white/10 bg-zinc-900/40">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-white/5 text-sm">
          <thead className="bg-zinc-900/80">
            <tr>
              {columns.map((col) => {
                const active = sort && sort.key === col.key;
                const clickable = col.sortable && onSort;
                return (
                  <th
                    key={col.key}
                    scope="col"
                    className={clsx(
                      'px-4 text-left text-xs font-semibold uppercase tracking-wider text-zinc-400',
                      padY,
                      col.className,
                      clickable && 'cursor-pointer select-none hover:text-white',
                    )}
                    onClick={clickable ? () => onSort(col.key) : undefined}
                  >
                    <span className="inline-flex items-center gap-1.5">
                      {col.header}
                      {col.sortable && (
                        <span className="inline-flex flex-col text-[10px] leading-none text-zinc-600">
                          <ChevronUpIcon
                            width={12}
                            height={12}
                            className={clsx(active && sort?.dir === 'asc' && 'text-red-400')}
                          />
                          <ChevronDownIcon
                            width={12}
                            height={12}
                            className={clsx(active && sort?.dir === 'desc' && 'text-red-400')}
                          />
                        </span>
                      )}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {rows.length === 0 && empty && (
              <tr>
                <td colSpan={columns.length} className="px-2 py-2">
                  {empty}
                </td>
              </tr>
            )}
            {rows.map((row) => (
              <tr
                key={rowKey(row)}
                className="transition hover:bg-white/[0.03]"
              >
                {columns.map((col) => (
                  <td key={col.key} className={clsx('px-4 text-zinc-200', padY, col.className)}>
                    {col.render(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Client-side sort helper. */
export function sortRows<T>(rows: T[], sort: SortState, getters: Record<string, (r: T) => string | number>) {
  const get = getters[sort.key];
  if (!get) return rows;
  const copy = [...rows];
  copy.sort((a, b) => {
    const av = get(a);
    const bv = get(b);
    if (av < bv) return sort.dir === 'asc' ? -1 : 1;
    if (av > bv) return sort.dir === 'asc' ? 1 : -1;
    return 0;
  });
  return copy;
}

/** Client-side pagination helper. */
export function paginate<T>(rows: T[], page: number, pageSize: number) {
  const total = rows.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const start = safePage * pageSize;
  return {
    page: safePage,
    pageCount,
    total,
    paged: rows.slice(start, start + pageSize),
  };
}
