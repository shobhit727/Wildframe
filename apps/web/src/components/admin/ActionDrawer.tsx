/**
 * ActionDrawer — Radix Dialog used for forms / confirmations / detail panels.
 * Accessible by default (focus trap, ESC to close, labelled title/description).
 */
'use client';

import * as Dialog from '@radix-ui/react-dialog';
import { ReactNode } from 'react';
import clsx from 'clsx';
import { XIcon } from './icons';

interface ActionDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: 'sm' | 'md' | 'lg';
}

const SIZES: Record<NonNullable<ActionDrawerProps['size']>, string> = {
  sm: 'max-w-md',
  md: 'max-w-xl',
  lg: 'max-w-3xl',
};

export function ActionDrawer({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  size = 'md',
}: ActionDrawerProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in" />
        <Dialog.Content
          className={clsx(
            'fixed left-1/2 top-1/2 z-50 w-full -translate-x-1/2 -translate-y-1/2',
            'rounded-2xl border border-white/10 bg-zinc-950 p-6 shadow-2xl',
            'data-[state=open]:animate-in data-[state=open]:fade-in data-[state=open]:slide-in-from-bottom-4',
            SIZES[size],
          )}
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-lg font-semibold text-white">{title}</Dialog.Title>
              {description && (
                <Dialog.Description className="mt-1 text-sm text-zinc-400">
                  {description}
                </Dialog.Description>
              )}
            </div>
            <Dialog.Close asChild>
              <button
                aria-label="Close"
                className="rounded-lg p-1.5 text-zinc-400 transition hover:bg-white/5 hover:text-white"
              >
                <XIcon />
              </button>
            </Dialog.Close>
          </div>

          <div className="mt-5 max-h-[60vh] overflow-y-auto pr-1">{children}</div>

          {footer && (
            <div className="mt-6 flex items-center justify-end gap-3 border-t border-white/10 pt-4">
              {footer}
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
