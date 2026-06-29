'use client';

import * as Dialog from '@radix-ui/react-dialog';
import { ReactNode } from 'react';

interface ModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
  title?: string;
  description?: string;
}

export function Modal({ open, onOpenChange, children, title, description }: ModalProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/70 backdrop-blur-sm data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out" />
        <Dialog.Content
          className="fixed z-50 top-[50%] left-[50%] translate-x-[-50%] translate-y-[-50%] w-[90vw] max-w-lg max-h-[85vh] overflow-y-auto bg-dark-900 border border-dark-700 rounded-xl p-6 shadow-2xl focus:outline-none animate-scale-in"
        >
          {title && (
            <Dialog.Title className="text-lg font-semibold text-white mb-1">
              {title}
            </Dialog.Title>
          )}
          {description && (
            <Dialog.Description className="text-sm text-gray-400 mb-4">
              {description}
            </Dialog.Description>
          )}
          {children}
          <Dialog.Close asChild>
            <button
              className="absolute top-4 right-4 text-gray-400 hover:text-white transition-colors"
              aria-label="Close"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
