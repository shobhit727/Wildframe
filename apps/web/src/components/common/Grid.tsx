'use client';

import { clsx } from 'clsx';

interface GridProps {
  children: React.ReactNode;
  cols?: 1 | 2 | 3 | 4;
  gap?: 'sm' | 'md' | 'lg';
}

const colClasses: Record<number, string> = {
  1: 'grid-cols-1',
  2: 'grid-cols-1 md:grid-cols-2',
  3: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3',
  4: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-4',
};

const gapClasses: Record<string, string> = {
  sm: 'gap-4',
  md: 'gap-8',
  lg: 'gap-12',
};

export function Grid({ children, cols = 3, gap = 'md' }: GridProps) {
  return <div className={clsx('grid', colClasses[cols], gapClasses[gap])}>{children}</div>;
}
