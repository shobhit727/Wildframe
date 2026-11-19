/**
 * ConfirmDialog — destructive-action confirmation (Radix Dialog).
 *
 * Destructive admin routes require step-up authentication, so the dialog can
 * collect the admin's password (and MFA code when enrolled) and hand it to
 * `onConfirm`; the caller exchanges it for a single-use `X-Admin-Reauth` token.
 */
'use client';

import * as Dialog from '@radix-ui/react-dialog';
import clsx from 'clsx';
import { ReactNode, useEffect, useState } from 'react';
import { Button } from '@/components/common/Button';
import { Field, Input } from './fields';

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  loading?: boolean;
  /** Collect the admin password before confirming (step-up authentication). */
  requirePassword?: boolean;
  /** Inline error from a failed step-up attempt. */
  passwordError?: string | null;
  onConfirm: (password?: string, mfaCode?: string) => void;
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  destructive = true,
  loading,
  requirePassword = false,
  passwordError,
  onConfirm,
}: ConfirmDialogProps) {
  const [password, setPassword] = useState('');
  const [mfaCode, setMfaCode] = useState('');

  useEffect(() => {
    setPassword('');
    setMfaCode('');
  }, [open]);

  const canConfirm = !loading && (!requirePassword || password.trim().length > 0);

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canConfirm) return;
    onConfirm(requirePassword ? password : undefined, mfaCode.trim() || undefined);
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm" />
        <Dialog.Content
          className={clsx(
            'fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2',
            'rounded-2xl border border-white/10 bg-zinc-950 p-6 shadow-2xl',
          )}
        >
          <Dialog.Title className="text-lg font-semibold text-white">{title}</Dialog.Title>
          {description && (
            <Dialog.Description className="mt-2 text-sm text-zinc-400">{description}</Dialog.Description>
          )}
          <form onSubmit={submit}>
            {requirePassword && (
              <div className="mt-4 space-y-3">
                <Field
                  label="Password"
                  hint="For security, confirm your password to continue."
                  error={passwordError ?? undefined}
                >
                  <Input
                    type="password"
                    autoComplete="current-password"
                    autoFocus
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </Field>
                <Field label="Authenticator code" hint="Only required if MFA is enabled.">
                  <Input
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    value={mfaCode}
                    onChange={(e) => setMfaCode(e.target.value)}
                  />
                </Field>
              </div>
            )}
            <div className="mt-6 flex items-center justify-end gap-3">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="!border-white/10 !text-zinc-200 hover:!bg-white/5"
                onClick={() => onOpenChange(false)}
              >
                {cancelLabel}
              </Button>
              <button
                type="submit"
                disabled={!canConfirm}
                className={clsx(
                  'rounded-lg px-4 py-2 text-sm font-medium text-white transition hover:scale-105 disabled:opacity-50',
                  destructive ? 'bg-red-600 hover:bg-red-500' : 'bg-zinc-700 hover:bg-zinc-600',
                )}
              >
                {loading ? 'Working…' : confirmLabel}
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
