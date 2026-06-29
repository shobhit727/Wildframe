/**
 * Admin — Users table with search, status filter, pagination, and moderation
 * actions (suspend / activate / ban / delete) via the admin-service endpoints.
 */
'use client';

import { useMemo, useState } from 'react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import clsx from 'clsx';
import {
  DataTable,
  Column,
  SortState,
  FilterBar,
  StatusBadge,
  EmptyState,
  ActionDrawer,
  ConfirmDialog,
  AdminButton,
  Icons,
} from '@/components/admin';
import type { AdminUser, UserStatus } from '@/types/admin';
import { useUsers, useModerateUser } from '@/hooks/admin';

const STATUS_FILTERS: { label: string; value: string }[] = [
  { label: 'All', value: '' },
  { label: 'Active', value: 'active' },
  { label: 'Suspended', value: 'suspended' },
  { label: 'Banned', value: 'banned' },
];

const PAGE_SIZE = 10;

export default function AdminUsersPage() {
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<SortState>({ key: 'created_at', dir: 'desc' });

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selected, setSelected] = useState<AdminUser | null>(null);
  const [action, setAction] = useState<{ type: UserStatus } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const query = useUsers({ limit: 100, offset: 0, status, search });
  const moderate = useModerateUser();

  const filtered = useMemo(() => {
    const rows = query.data ?? [];
    if (!search) return rows;
    const q = search.toLowerCase();
    return rows.filter(
      (u) =>
        u.email?.toLowerCase().includes(q) ||
        u.first_name?.toLowerCase().includes(q) ||
        u.last_name?.toLowerCase().includes(q) ||
        u.user_id.toLowerCase().includes(q),
    );
  }, [query.data, search]);

  const sorted = useMemo(
    () =>
      [...filtered].sort((a, b) => {
        const av = (a as any)[sort.key] ?? '';
        const bv = (b as any)[sort.key] ?? '';
        if (av < bv) return sort.dir === 'asc' ? -1 : 1;
        if (av > bv) return sort.dir === 'asc' ? 1 : -1;
        return 0;
      }),
    [filtered, sort],
  );

  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const paged = sorted.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  const requestAction = (user: AdminUser, type: UserStatus) => {
    setSelected(user);
    setAction({ type });
    setConfirmOpen(true);
  };

  const confirm = () => {
    if (!selected || !action) return;
    moderate.mutate(
      { user_id: selected.user_id, status: action.type, reason: selected.reason ?? undefined },
      { onSuccess: () => setConfirmOpen(false) },
    );
  };

  const columns: Column<AdminUser>[] = [
    {
      key: 'user',
      header: 'User',
      sortable: true,
      render: (u) => (
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 grid place-items-center rounded-full bg-red-600/15 text-xs font-bold uppercase text-red-400">
            {(u.first_name?.[0] ?? 'U').toUpperCase()}
            {(u.last_name?.[0] ?? '').toUpperCase()}
          </div>
          <div className="min-w-0">
            <p className="truncate font-medium text-white">
              {u.first_name} {u.last_name}
            </p>
            <p className="truncate text-xs text-zinc-500">{u.email}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'user_id',
      header: 'ID',
      sortable: true,
      className: 'hidden md:table-cell',
      render: (u) => <span className="font-mono text-xs text-zinc-400">{u.user_id.slice(0, 12)}…</span>,
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      render: (u) => <StatusBadge status={u.status} />,
    },
    {
      key: 'created_at',
      header: 'Created',
      sortable: true,
      className: 'hidden lg:table-cell',
      render: (u) => (
        <span className="text-xs text-zinc-400">
          {u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (u) => (
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button
              aria-label="Row actions"
              className="rounded-lg p-2 text-zinc-400 transition hover:bg-white/5 hover:text-white"
            >
              <Icons.MoreIcon />
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content
              align="end"
              className="z-50 min-w-[180px] rounded-lg border border-white/10 bg-zinc-950 p-1 shadow-xl"
              sideOffset={6}
            >
              <DropdownMenu.Item
                onSelect={() => {
                  setSelected(u);
                  setDrawerOpen(true);
                }}
                className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-2 text-sm text-zinc-200 outline-none hover:bg-white/5 data-[highlighted]:bg-white/5"
              >
                <Icons.ClipboardIcon width={15} height={15} /> View details
              </DropdownMenu.Item>
              <DropdownMenu.Separator className="my-1 h-px bg-white/10" />
              <DropdownMenu.Item
                onSelect={() => requestAction(u, 'active')}
                className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-2 text-sm text-green-400 outline-none hover:bg-white/5 data-[highlighted]:bg-white/5"
              >
                <Icons.PlayIcon width={15} height={15} /> Activate
              </DropdownMenu.Item>
              <DropdownMenu.Item
                onSelect={() => requestAction(u, 'suspended')}
                className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-2 text-sm text-amber-400 outline-none hover:bg-white/5 data-[highlighted]:bg-white/5"
              >
                <Icons.BanIcon width={15} height={15} /> Suspend
              </DropdownMenu.Item>
              <DropdownMenu.Item
                onSelect={() => requestAction(u, 'banned')}
                className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-2 text-sm text-red-400 outline-none hover:bg-white/5 data-[highlighted]:bg-white/5"
              >
                <Icons.XIcon width={15} height={15} /> Ban
              </DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white sm:text-3xl">Users</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Manage platform users and moderation status.
        </p>
      </div>

      <FilterBar
        value={search}
        onChange={(v) => {
          setSearch(v);
          setPage(0);
        }}
        placeholder="Search by name, email, or ID…"
        left={
          <div className="flex items-center gap-1 rounded-lg border border-white/10 bg-zinc-900/60 p-1">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.value}
                onClick={() => {
                  setStatus(f.value);
                  setPage(0);
                }}
                className={clsx(
                  'rounded-md px-3 py-1.5 text-xs font-medium transition',
                  status === f.value
                    ? 'bg-red-600/20 text-red-300'
                    : 'text-zinc-400 hover:bg-white/5 hover:text-white',
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
        }
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
          rowKey={(u) => u.user_id}
          sort={sort}
          onSort={(key) => setSort({ key, dir: sort.key === key && sort.dir === 'asc' ? 'desc' : 'asc' })}
          empty={
            <EmptyState
              icon={<Icons.UsersIcon />}
              title="No users found"
              description="Try adjusting your search or filters."
            />
          }
        />
      )}

      {/* Pagination */}
      <div className="flex items-center justify-between text-sm text-zinc-400">
        <p>
          {filtered.length} user{filtered.length === 1 ? '' : 's'}
        </p>
        <div className="flex items-center gap-2">
          <AdminButton
            variant="secondary"
            size="sm"
            disabled={safePage === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            Previous
          </AdminButton>
          <span className="tabular-nums">
            {safePage + 1} / {pageCount}
          </span>
          <AdminButton
            variant="secondary"
            size="sm"
            disabled={safePage >= pageCount - 1}
            onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
          >
            Next
          </AdminButton>
        </div>
      </div>

      {/* Detail drawer */}
      <ActionDrawer
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        title="User details"
        description={selected?.email}
        size="md"
      >
        {selected && (
          <div className="space-y-4 text-sm">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-xs text-zinc-500">Name</p>
                <p className="text-white">
                  {selected.first_name} {selected.last_name}
                </p>
              </div>
              <div>
                <p className="text-xs text-zinc-500">Status</p>
                <StatusBadge status={selected.status} />
              </div>
              <div className="col-span-2">
                <p className="text-xs text-zinc-500">User ID</p>
                <p className="font-mono text-zinc-300">{selected.user_id}</p>
              </div>
              <div className="col-span-2">
                <p className="text-xs text-zinc-500">Email</p>
                <p className="text-zinc-300">{selected.email}</p>
              </div>
              <div className="col-span-2">
                <p className="text-xs text-zinc-500">Last moderation reason</p>
                <p className="text-zinc-300">{selected.reason ?? '—'}</p>
              </div>
            </div>
          </div>
        )}
      </ActionDrawer>

      {/* Confirm dialog */}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={action ? `${action.type === 'active' ? 'Activate' : action.type === 'suspended' ? 'Suspend' : 'Ban'} user` : 'Confirm'}
        description={
          selected
            ? `You are about to set ${selected.first_name} ${selected.last_name} (${selected.email}) to "${action?.type}". This will take effect immediately.`
            : undefined
        }
        confirmLabel={action ? (action.type === 'active' ? 'Activate' : action.type === 'suspended' ? 'Suspend' : 'Ban') : 'Confirm'}
        destructive={action?.type !== 'active'}
        loading={moderate.isPending}
        onConfirm={confirm}
      />
    </div>
  );
}
