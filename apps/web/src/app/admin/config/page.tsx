/**
 * Admin — key/value system config editor (GET current, PUT update).
 */
'use client';

import { useMemo, useState } from 'react';
import {
  FilterBar,
  ActionDrawer,
  AdminButton,
  Field,
  Input,
  Textarea,
  Select,
  Icons,
} from '@/components/admin';
import type { ConfigType, SystemConfig } from '@/types/admin';
import { useConfigs, useSetConfig } from '@/hooks/admin';

const CONFIG_TYPES: { label: string; value: ConfigType }[] = [
  { label: 'String', value: 'string' },
  { label: 'Integer', value: 'integer' },
  { label: 'Boolean', value: 'boolean' },
  { label: 'JSON', value: 'json' },
];

export default function AdminConfigPage() {
  const [search, setSearch] = useState('');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<SystemConfig | null>(null);
  const [creating, setCreating] = useState(false);

  const [form, setForm] = useState({
    key: '',
    value: '',
    config_type: 'string' as ConfigType,
    description: '',
  });

  const configs = useConfigs({ limit: 200 });
  const setConfig = useSetConfig();

  const rows = useMemo(() => {
    const all = configs.data ?? [];
    if (!search) return all;
    const q = search.toLowerCase();
    return all.filter(
      (c) =>
        c.key.toLowerCase().includes(q) ||
        c.value.toLowerCase().includes(q) ||
        (c.description ?? '').toLowerCase().includes(q),
    );
  }, [configs.data, search]);

  const openEdit = (c: SystemConfig) => {
    setEditing(c);
    setForm({ key: c.key, value: c.value, config_type: c.config_type as ConfigType, description: c.description ?? '' });
    setDrawerOpen(true);
  };

  const openCreate = () => {
    setEditing(null);
    setForm({ key: '', value: '', config_type: 'string', description: '' });
    setCreating(true);
    setDrawerOpen(true);
  };

  const submit = () => {
    setConfig.mutate(form, {
      onSuccess: () => {
        setDrawerOpen(false);
        setCreating(false);
        setEditing(null);
      },
    });
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white sm:text-3xl">System Config</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Manage runtime configuration keys across services.
          </p>
        </div>
        <AdminButton onClick={openCreate}>
          <Icons.PlusIcon width={16} height={16} /> New config
        </AdminButton>
      </div>

      <FilterBar
        value={search}
        onChange={setSearch}
        placeholder="Search by key, value, or description…"
      />

      {configs.isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-12 animate-pulse rounded-lg bg-zinc-800/60" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-white/10 bg-zinc-900/40 px-6 py-16 text-center">
          <Icons.SettingsIcon className="mb-3 text-zinc-600" />
          <h3 className="text-base font-semibold text-white">No config entries</h3>
          <p className="mt-1 text-sm text-zinc-400">Create a key to get started.</p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-white/10">
          <table className="min-w-full divide-y divide-white/5 text-sm">
            <thead className="bg-zinc-900/80">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-zinc-400">Key</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-zinc-400">Value</th>
                <th className="hidden px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-zinc-400 md:table-cell">Type</th>
                <th className="hidden px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-zinc-400 lg:table-cell">Description</th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider text-zinc-400">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {rows.map((c) => (
                <tr key={c.id} className="transition hover:bg-white/[0.03]">
                  <td className="px-4 py-3">
                    <code className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-xs text-red-300">
                      {c.key}
                    </code>
                  </td>
                  <td className="max-w-xs truncate px-4 py-3 font-mono text-xs text-zinc-300">
                    {c.value}
                  </td>
                  <td className="hidden px-4 py-3 text-xs text-zinc-400 md:table-cell">{c.config_type}</td>
                  <td className="hidden max-w-sm truncate px-4 py-3 text-xs text-zinc-500 lg:table-cell">
                    {c.description ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <AdminButton variant="secondary" size="sm" onClick={() => openEdit(c)}>
                      Edit
                    </AdminButton>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ActionDrawer
        open={drawerOpen}
        onOpenChange={(o) => {
          if (!o) {
            setCreating(false);
            setEditing(null);
          }
          setDrawerOpen(o);
        }}
        title={editing ? `Edit "${editing.key}"` : creating ? 'New config entry' : 'Config entry'}
        description="Changes take effect on the next service poll."
        footer={
          <>
            <AdminButton
              variant="ghost"
              onClick={() => {
                setDrawerOpen(false);
                setCreating(false);
                setEditing(null);
              }}
              disabled={setConfig.isPending}
            >
              Cancel
            </AdminButton>
            <AdminButton
              onClick={submit}
              disabled={setConfig.isPending || !form.key || !form.value}
            >
              {setConfig.isPending ? 'Saving…' : 'Save'}
            </AdminButton>
          </>
        }
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Key" hint="Unique identifier, e.g. rate_limit.rps">
            <Input
              value={form.key}
              onChange={(e) => setForm((f) => ({ ...f, key: e.target.value }))}
              disabled={!!editing}
              placeholder="feature.flag_name"
            />
          </Field>
          <Field label="Type">
            <Select
              value={form.config_type}
              onChange={(e) => setForm((f) => ({ ...f, config_type: e.target.value as ConfigType }))}
            >
              {CONFIG_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Value" hint={form.config_type === 'json' ? 'Valid JSON string' : undefined}>
            <Textarea
              value={form.value}
              onChange={(e) => setForm((f) => ({ ...f, value: e.target.value }))}
              placeholder={form.config_type === 'json' ? '{"enabled": true}' : 'value'}
            />
          </Field>
          <Field label="Description" hint="Optional context for operators.">
            <Input
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              placeholder="What does this control?"
            />
          </Field>
        </div>
      </ActionDrawer>
    </div>
  );
}
