/**
 * Admin — system alerts list with create + acknowledge actions.
 */
'use client';

import { useState } from 'react';
import {
  StatusBadge,
  ActionDrawer,
  ConfirmDialog,
  AdminButton,
  Field,
  Input,
  Textarea,
  Select,
  Icons,
} from '@/components/admin';
import type { AlertSeverity, SystemAlert } from '@/types/admin';
import { useAlerts, useCreateAlert, useAcknowledgeAlert } from '@/hooks/admin';

const SEVERITY: { label: string; value: AlertSeverity }[] = [
  { label: 'Info', value: 'info' },
  { label: 'Warning', value: 'warning' },
  { label: 'Critical', value: 'critical' },
];

function SeverityBadge({ severity }: { severity: string }) {
  const tone =
    severity === 'critical' ? 'red' : severity === 'warning' ? 'amber' : 'sky';
  return <StatusBadge status={severity} tone={tone} />;
}

export default function AdminAlertsPage() {
  const alerts = useAlerts({ limit: 50 });
  const create = useCreateAlert();
  const ack = useAcknowledgeAlert();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [ackTarget, setAckTarget] = useState<SystemAlert | null>(null);
  const [form, setForm] = useState({
    alert_type: '',
    severity: 'warning' as AlertSeverity,
    message: '',
    service: '',
  });

  const submit = () => {
    create.mutate(form, {
      onSuccess: () => {
        setDrawerOpen(false);
        setForm({ alert_type: '', severity: 'warning', message: '', service: '' });
      },
    });
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white sm:text-3xl">System Alerts</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Monitor and broadcast operational alerts across services.
          </p>
        </div>
        <AdminButton onClick={() => setDrawerOpen(true)}>
          <Icons.PlusIcon width={16} height={16} /> New alert
        </AdminButton>
      </div>

      {alerts.isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-16 animate-pulse rounded-lg bg-zinc-800/60" />
          ))}
        </div>
      ) : (alerts.data?.length ?? 0) === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-white/10 bg-zinc-900/40 px-6 py-16 text-center">
          <Icons.BellIcon className="mb-3 text-zinc-600" />
          <h3 className="text-base font-semibold text-white">All clear</h3>
          <p className="mt-1 text-sm text-zinc-400">No unacknowledged alerts right now.</p>
        </div>
      ) : (
        <ul className="space-y-3">
          {(alerts.data ?? []).map((a) => (
            <li
              key={a.id}
              className="flex flex-col gap-3 rounded-xl border border-white/10 bg-zinc-900/40 p-4 transition hover:border-white/20 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="flex items-start gap-3">
                <div className="mt-0.5 rounded-lg bg-white/5 p-2 text-zinc-400">
                  <Icons.BellIcon />
                </div>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold text-white">{a.alert_type}</span>
                    <SeverityBadge severity={a.severity} />
                    <span className="rounded-full bg-white/5 px-2 py-0.5 text-[11px] text-zinc-400">
                      {a.service}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-zinc-300">{a.message}</p>
                  <p className="mt-1 text-xs text-zinc-500">
                    {new Date(a.created_at).toLocaleString()}
                    {a.acknowledged && (
                      <span className="ml-2 text-zinc-600">
                        · acknowledged{a.acknowledged_by ? ` by ${a.acknowledged_by}` : ''}
                      </span>
                    )}
                  </p>
                </div>
              </div>
              {!a.acknowledged && (
                <AdminButton
                  variant="secondary"
                  size="sm"
                  disabled={ack.isPending}
                  onClick={() => setAckTarget(a)}
                >
                  <Icons.CheckIcon width={15} height={15} /> Acknowledge
                </AdminButton>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* Create alert drawer */}
      <ActionDrawer
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        title="Create system alert"
        description="Broadcast an alert to operators across services."
        footer={
          <>
            <AdminButton variant="ghost" onClick={() => setDrawerOpen(false)} disabled={create.isPending}>
              Cancel
            </AdminButton>
            <AdminButton onClick={submit} disabled={create.isPending || !form.alert_type || !form.message || !form.service}>
              {create.isPending ? 'Creating…' : 'Create alert'}
            </AdminButton>
          </>
        }
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Alert type">
            <Input
              value={form.alert_type}
              onChange={(e) => setForm((f) => ({ ...f, alert_type: e.target.value }))}
              placeholder="e.g. high-latency"
            />
          </Field>
          <Field label="Service">
            <Input
              value={form.service}
              onChange={(e) => setForm((f) => ({ ...f, service: e.target.value }))}
              placeholder="e.g. streaming-service"
            />
          </Field>
          <Field label="Severity">
            <Select
              value={form.severity}
              onChange={(e) => setForm((f) => ({ ...f, severity: e.target.value as AlertSeverity }))}
            >
              {SEVERITY.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Message" hint="Short human-readable description.">
            <Textarea
              value={form.message}
              onChange={(e) => setForm((f) => ({ ...f, message: e.target.value }))}
              placeholder="Describe the issue…"
            />
          </Field>
        </div>
      </ActionDrawer>

      <ConfirmDialog
        open={!!ackTarget}
        onOpenChange={(o) => !o && setAckTarget(null)}
        title="Acknowledge alert"
        description={ackTarget ? `"${ackTarget.alert_type}" on ${ackTarget.service} will be marked as resolved.` : undefined}
        confirmLabel="Acknowledge"
        destructive={false}
        loading={ack.isPending}
        onConfirm={() => {
          if (!ackTarget) return;
          ack.mutate(ackTarget.id, { onSuccess: () => setAckTarget(null) });
        }}
      />
    </div>
  );
}
