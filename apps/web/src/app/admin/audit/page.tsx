/**
 * Admin — audit log viewer (filterable table).
 */
'use client';

import { useCallback, useMemo, useState } from 'react';
import {
  DataTable,
  Column,
  SortState,
  FilterBar,
  EmptyState,
  Field,
  Select,
  Icons,
} from '@/components/admin';
import type { AuditLog } from '@/types/admin';
import { useAuditLogs } from '@/hooks/admin';

const ACTIONS = [
  { label: 'All actions', value: '' },
  { label: 'User moderation', value: 'user_moderation' },
  { label: 'Content flag', value: 'content_flagged' },
  { label: 'Content resolve', value: 'content_resolved' },
  { label: 'Config change', value: 'set_config' },
];

/**
 * How much of the trail this page looks at.
 *
 * The default is deliberately the widest window: a narrower default would
 * silently hide entries from an operator investigating a privileged action,
 * which is the failure mode #949 was about. The bounded windows exist to scope
 * a busy log, and whichever one is active is named in the empty state so
 * "nothing here" is never unqualified.
 */
const RANGES = [
  { label: 'All time', value: 'all', hours: null },
  { label: 'Last 24 hours', value: '24h', hours: 24 },
  { label: 'Last 7 days', value: '7d', hours: 24 * 7 },
  { label: 'Last 30 days', value: '30d', hours: 24 * 30 },
] as const;

const HOUR_MS = 60 * 60 * 1000;
const FETCH_LIMIT = 100;

export default function AdminAuditPage() {
  const [search, setSearch] = useState('');
  const [adminId, setAdminId] = useState('');
  const [action, setAction] = useState('');
  const [range, setRange] = useState<string>('all');
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<SortState>({ key: 'created_at', dir: 'desc' });

  const query = useAuditLogs({
    limit: FETCH_LIMIT,
    admin_id: adminId || undefined,
  });

  /**
   * The window is anchored when the operator picks it, not on every render:
   * reading the clock during render is impure, and a moving boundary would make
   * rows appear and vanish with no user action — on the one screen where a
   * row quietly disappearing is the defect.
   */
  const [cutoff, setCutoff] = useState<number | null>(null);

  const rangeLabel = RANGES.find((r) => r.value === range)?.label ?? 'All time';
  const rangeHours = RANGES.find((r) => r.value === range)?.hours ?? null;

  const applyRange = useCallback((value: string) => {
    const hours = RANGES.find((r) => r.value === value)?.hours ?? null;
    setRange(value);
    setCutoff(hours === null ? null : Date.now() - hours * HOUR_MS);
    setPage(0);
  }, []);

  // Every active narrowing, named. Drives both the row filter and the empty
  // state, so the two can never disagree about why the table is empty.
  const activeFilters = useMemo(
    () =>
      [
        search.trim() && `search "${search.trim()}"`,
        adminId.trim() && `admin "${adminId.trim()}"`,
        action && `action "${action}"`,
        rangeHours !== null && rangeLabel.toLowerCase(),
      ].filter(Boolean) as string[],
    [search, adminId, action, rangeHours, rangeLabel],
  );

  const rows = useMemo(() => {
    const all = query.data ?? [];
    const filtered = all.filter((r) => {
      const matchesAction = !action || r.action.toLowerCase().startsWith(action);
      const q = search.toLowerCase();
      const matchesSearch =
        !q ||
        r.admin_id.toLowerCase().includes(q) ||
        r.action.toLowerCase().includes(q) ||
        r.resource_type.toLowerCase().includes(q) ||
        r.resource_id.toLowerCase().includes(q);
      const matchesRange = cutoff === null || Date.parse(r.created_at) >= cutoff;
      return matchesAction && matchesSearch && matchesRange;
    });
    return [...filtered].sort((a, b) => {
      const av = (a as any)[sort.key] ?? '';
      const bv = (b as any)[sort.key] ?? '';
      if (av < bv) return sort.dir === 'asc' ? -1 : 1;
      if (av > bv) return sort.dir === 'asc' ? 1 : -1;
      return 0;
    });
  }, [query.data, search, action, sort, cutoff]);

  const clearFilters = useCallback(() => {
    setSearch('');
    setAdminId('');
    setAction('');
    setRange('all');
    setCutoff(null);
    setPage(0);
  }, []);

  /**
   * Three states an operator must be able to tell apart: the trail could not be
   * read, the trail was read and the filters excluded everything, or the trail
   * is genuinely empty. Collapsing the last two is what let a broken filter
   * read as "nobody did anything".
   */
  const empty = useMemo(() => {
    if (query.isError) {
      const reason = query.error instanceof Error ? query.error.message : 'unknown error';
      return {
        icon: <Icons.ClipboardIcon />,
        title: 'Audit log unavailable',
        description: `The audit trail could not be read (${reason}), so this is not an empty log. Nothing below is evidence that no privileged action took place.`,
      };
    }
    if (activeFilters.length > 0) {
      return {
        icon: <Icons.ClipboardIcon />,
        title: 'No entries match the current filter',
        description: `Filtered by ${activeFilters.join(', ')}. Entries may exist outside this view — clear the filters to see everything the trail returned for ${rangeLabel.toLowerCase()}.`,
        action: (
          <button
            className="rounded-lg border border-white/10 px-3 py-1.5 text-xs font-medium text-zinc-300 transition hover:bg-white/5"
            onClick={clearFilters}
          >
            Clear filters
          </button>
        ),
      };
    }
    return {
      icon: <Icons.ClipboardIcon />,
      title: 'No audit entries',
      description: 'Actions by admins will be recorded here.',
    };
  }, [query.isError, query.error, activeFilters, rangeLabel, clearFilters]);

  // A full page of results means the trail continues past what was fetched.
  // Silence here reads as "that is everything", so say otherwise.
  const truncated = (query.data?.length ?? 0) >= FETCH_LIMIT;

  const PAGE_SIZE = 12;
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const paged = rows.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  const columns: Column<AuditLog>[] = [
    {
      key: 'created_at',
      header: 'When',
      sortable: true,
      render: (r) => (
        <span className="text-xs text-zinc-400">{new Date(r.created_at).toLocaleString()}</span>
      ),
    },
    {
      key: 'admin_id',
      header: 'Admin',
      sortable: true,
      render: (r) => <span className="font-mono text-xs text-zinc-300">{r.admin_id}</span>,
    },
    {
      key: 'action',
      header: 'Action',
      sortable: true,
      render: (r) => (
        <code className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[11px] text-red-300">
          {r.action}
        </code>
      ),
    },
    {
      key: 'resource',
      header: 'Resource',
      render: (r) => (
        <span className="text-xs text-zinc-300">
          {r.resource_type}
          <span className="text-zinc-500"> · </span>
          <span className="font-mono text-zinc-400">{r.resource_id.slice(0, 10)}…</span>
        </span>
      ),
    },
    {
      key: 'changes',
      header: 'Changes',
      className: 'hidden lg:table-cell',
      render: (r) => (
        <span className="block max-w-xs truncate font-mono text-[11px] text-zinc-500">
          {r.changes ?? '—'}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white sm:text-3xl">Audit Log</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Immutable record of privileged actions across the platform.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-5">
        <div className="lg:col-span-2">
          <FilterBar value={search} onChange={(v) => { setSearch(v); setPage(0); }} placeholder="Search admin, action, resource…" />
        </div>
        <Field label="Admin ID">
          <input
            type="text"
            value={adminId}
            onChange={(e) => { setAdminId(e.target.value); setPage(0); }}
            placeholder="Filter by admin…"
            className="w-full rounded-lg border border-white/10 bg-zinc-900/70 px-3 py-2 text-sm text-white placeholder:text-zinc-500 focus:border-red-500/50 focus:outline-none focus:ring-2 focus:ring-red-500/30"
          />
        </Field>
        <Field label="Action">
          <Select value={action} onChange={(e) => { setAction(e.target.value); setPage(0); }}>
            {ACTIONS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Time range">
          <Select value={range} onChange={(e) => applyRange(e.target.value)}>
            {RANGES.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {query.isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-10 animate-pulse rounded-lg bg-zinc-800/60" />
          ))}
        </div>
      ) : (
        <DataTable
          columns={columns}
          rows={paged}
          rowKey={(r) => String(r.id)}
          sort={sort}
          onSort={(key) => setSort({ key, dir: sort.key === key && sort.dir === 'asc' ? 'desc' : 'asc' })}
          empty={<EmptyState {...empty} />}
        />
      )}

      <div className="flex items-center justify-between text-sm text-zinc-400">
        <div className="flex flex-col gap-1">
          <p>
            {rows.length} entr{rows.length === 1 ? 'y' : 'ies'}
          </p>
          {truncated && (
            <p className="text-xs text-amber-400/90">
              Showing the most recent {FETCH_LIMIT} entries. Older entries exist and are not
              loaded — narrow a window to search further back.
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            className="rounded-lg border border-white/10 px-3 py-1.5 text-xs font-medium transition hover:bg-white/5 disabled:opacity-40"
            disabled={safePage === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            Previous
          </button>
          <span className="tabular-nums">
            {safePage + 1} / {pageCount}
          </span>
          <button
            className="rounded-lg border border-white/10 px-3 py-1.5 text-xs font-medium transition hover:bg-white/5 disabled:opacity-40"
            disabled={safePage >= pageCount - 1}
            onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
