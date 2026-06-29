/**
 * ConfirmDialog — destructive-action confirmation (Radix Dialog).
 */
'use client';

import * as Dialog from '@radix-ui/react-dialog';
import clsx from 'clsx';
import { ReactNode } from 'react';
import { Button } from '@/components/common/Button';

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  loading?: boolean;
  onConfirm: () => void;
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
  onConfirm,
}: ConfirmDialogProps) {
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
          <div className="mt-6 flex items-center justify-end gap-3">
            <Button
              variant="secondary"
              size="sm"
              className="!border-white/10 !text-zinc-200 hover:!bg-white/5"
              onClick={() => onOpenChange(false)}
            >
              {cancelLabel}
            </Button>
            <button
              disabled={loading}
              onClick={onConfirm}
              className={clsx(
                'rounded-lg px-4 py-2 text-sm font-medium text-white transition hover:scale-105 disabled:opacity-50',
                destructive ? 'bg-red-600 hover:bg-red-500' : 'bg-zinc-700 hover:bg-zinc-600',
              )}
            >
              {loading ? 'Working…' : confirmLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
