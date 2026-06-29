/**
 * Admin — Content flags queue with resolve / dismiss actions.
 */
'use client';

import { useMemo, useState } from 'react';
import {
  DataTable,
  Column,
  SortState,
  FilterBar,
  StatusBadge,
  EmptyState,
  ConfirmDialog,
  AdminButton,
  Icons,
} from '@/components/admin';
import type { ContentFlag, ContentStatus } from '@/types/admin';
import { useFlags, useResolveFlag } from '@/hooks/admin';

const PAGE_SIZE = 10;

export default function AdminFlagsPage() {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<SortState>({ key: 'flagged_at', dir: 'desc' });
  const [pending, setPending] = useState<{ flag: ContentFlag; status: ContentStatus } | null>(null);

  const query = useFlags({ limit: 100 });
  const resolve = useResolveFlag();

  const rows = useMemo(() => {
    const all = query.data ?? [];
    const filtered = search
      ? all.filter(
          (f) =>
            f.content_id.toLowerCase().includes(search.toLowerCase()) ||
            f.content_type.toLowerCase().includes(search.toLowerCase()) ||
            (f.reason ?? '').toLowerCase().includes(search.toLowerCase()),
        )
      : all;
    return [...filtered].sort((a, b) => {
      const av = (a as any)[sort.key] ?? '';
      const bv = (b as any)[sort.key] ?? '';
      if (av < bv) return sort.dir === 'asc' ? -1 : 1;
      if (av > bv) return sort.dir === 'asc' ? 1 : -1;
      return 0;
    });
  }, [query.data, search, sort]);

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const paged = rows.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  const columns: Column<ContentFlag>[] = [
    {
      key: 'content',
      header: 'Content',
      render: (f) => (
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 grid place-items-center rounded-lg bg-purple-500/15 text-xs font-bold uppercase text-purple-400">
            {f.content_type.slice(0, 2)}
          </div>
          <div className="min-w-0">
            <p className="truncate font-mono text-xs text-white">{f.content_id.slice(0, 16)}…</p>
            <p className="text-xs text-zinc-500">{f.content_type}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'reason',
      header: 'Reason',
      className: 'hidden md:table-cell',
      render: (f) => <span className="text-sm text-zinc-300">{f.reason ?? '—'}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      render: (f) => <StatusBadge status={f.status} />,
    },
    {
      key: 'flagged_at',
      header: 'Flagged',
      sortable: true,
      className: 'hidden lg:table-cell',
      render: (f) => (
        <span className="text-xs text-zinc-400">
          {f.flagged_at ? new Date(f.flagged_at).toLocaleString() : '—'}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (f) => (
        <div className="flex items-center gap-1">
          <AdminButton
            variant="secondary"
            size="sm"
            disabled={resolve.isPending}
            onClick={() => setPending({ flag: f, status: 'active' })}
          >
            <Icons.CheckIcon width={15} height={15} /> Resolve
          </AdminButton>
          <AdminButton
            variant="ghost"
            size="sm"
            disabled={resolve.isPending}
            onClick={() => setPending({ flag: f, status: 'removed' })}
          >
            <Icons.TrashIcon width={15} height={15} /> Dismiss
          </AdminButton>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white sm:text-3xl">Content Flags</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Review flagged content and resolve or remove it.
        </p>
      </div>

      <FilterBar
        value={search}
        onChange={(v) => {
          setSearch(v);
          setPage(0);
        }}
        placeholder="Search by content ID, type, or reason…"
      />

      {query.isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-12 animate-pulse rounded-lg bg-zinc-800/60" />
          ))}
        </div>
      ) : (
        <DataTable
          columns={columns}
          rows={paged}
          rowKey={(f) => String(f.id)}
          sort={sort}
          onSort={(key) => setSort({ key, dir: sort.key === key && sort.dir === 'asc' ? 'desc' : 'asc' })}
          empty={
            <EmptyState
              icon={<Icons.FlagIcon />}
              title="No open flags"
              description="The moderation queue is clear."
            />
          }
        />
      )}

      <div className="flex items-center justify-between text-sm text-zinc-400">
        <p>
          {rows.length} flag{rows.length === 1 ? '' : 's'}
        </p>
        <div className="flex items-center gap-2">
          <AdminButton variant="secondary" size="sm" disabled={safePage === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>
            Previous
          </AdminButton>
          <span className="tabular-nums">
            {safePage + 1} / {pageCount}
          </span>
          <AdminButton variant="secondary" size="sm" disabled={safePage >= pageCount - 1} onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}>
            Next
          </AdminButton>
        </div>
      </div>

      <ConfirmDialog
        open={!!pending}
        onOpenChange={(o) => !o && setPending(null)}
        title={pending?.status === 'removed' ? 'Dismiss flag & remove content' : 'Resolve flag'}
        description={
          pending
            ? `Content ${pending.flag.content_id} (type: ${pending.flag.content_type}) will be set to "${pending.status}".`
            : undefined
        }
        confirmLabel={pending?.status === 'removed' ? 'Remove' : 'Resolve'}
        destructive={pending?.status === 'removed'}
        loading={resolve.isPending}
        onConfirm={() => {
          if (!pending) return;
          resolve.mutate(
            { content_id: pending.flag.content_id, status: pending.status },
            { onSuccess: () => setPending(null) },
          );
        }}
      />
    </div>
  );
}
