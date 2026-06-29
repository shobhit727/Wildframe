/**
 * Dark-theme button variants for the admin area.
 * Wraps native button; complements the light-themed common/Button.
 */
import clsx from 'clsx';
import { forwardRef, ButtonHTMLAttributes } from 'react';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-red-600 text-white hover:bg-red-500',
  secondary: 'border border-white/15 bg-white/5 text-zinc-100 hover:bg-white/10',
  ghost: 'text-zinc-300 hover:bg-white/5 hover:text-white',
  danger: 'bg-red-600/90 text-white hover:bg-red-500',
};

const SIZES: Record<Size, string> = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
  lg: 'px-5 py-2.5 text-sm',
};

export const AdminButton = forwardRef<HTMLButtonElement, Props>(
  ({ variant = 'primary', size = 'md', className, ...rest }, ref) => (
    <button
      ref={ref}
      className={clsx(
        'inline-flex items-center justify-center gap-2 rounded-lg font-medium transition hover:scale-105 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:scale-100',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    />
  ),
);

AdminButton.displayName = 'AdminButton';
