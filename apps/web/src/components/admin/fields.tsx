/**
 * Dark-themed form field primitives for admin drawers/forms.
 */
import clsx from 'clsx';
import { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes, forwardRef } from 'react';

const inputCls = clsx(
  'w-full rounded-lg border border-white/10 bg-zinc-900/70 px-3 py-2 text-sm text-white',
  'placeholder:text-zinc-500 focus:border-red-500/50 focus:outline-none focus:ring-2 focus:ring-red-500/30',
);

export const Label = ({ children, htmlFor }: { children: ReactNode; htmlFor?: string }) => (
  <label htmlFor={htmlFor} className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-zinc-400">
    {children}
  </label>
);

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...rest }, ref) => (
    <input ref={ref} className={clsx(inputCls, className)} {...rest} />
  ),
);
Input.displayName = 'Input';

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...rest }, ref) => (
    <textarea ref={ref} className={clsx(inputCls, 'min-h-[88px] resize-y', className)} {...rest} />
  ),
);
Textarea.displayName = 'Textarea';

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...rest }, ref) => (
    <select ref={ref} className={clsx(inputCls, 'appearance-none pr-8', className)} {...rest}>
      {children}
    </select>
  ),
);
Select.displayName = 'Select';

export const Field = ({
  label,
  children,
  hint,
  error,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
  error?: string;
}) => (
  <div>
    <Label>{label}</Label>
    {children}
    {hint && !error && <p className="mt-1 text-xs text-zinc-500">{hint}</p>}
    {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
  </div>
);
