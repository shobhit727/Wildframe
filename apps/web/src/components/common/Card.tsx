'use client';

import { clsx } from 'clsx';

interface CardProps {
  children: React.ReactNode;
  className?: string;
  hover?: boolean;
}

export function Card({ children, className = '', hover = true }: CardProps) {
  return (
    <div
      className={clsx(
        'bg-dark-900 p-6 rounded-xl border border-dark-800 transition',
        hover && 'hover:shadow-lg hover:border-dark-600',
        className
      )}
    >
      {children}
    </div>
  );
}
