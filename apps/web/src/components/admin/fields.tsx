/**
 * Dark-themed form field primitives for admin drawers/forms.
 */
import clsx from 'clsx';
import {
  InputHTMLAttributes,
  ReactElement,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
  cloneElement,
  forwardRef,
  isValidElement,
  useId,
} from 'react';

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

/**
 * Label + control, associated.
 *
 * The visible label is only useful to a screen reader if it points at the
 * control, so `Field` owns the id: it writes one onto the child and hands the
 * same value to the label's `htmlFor`. A child that declares its own id keeps
 * it and the label targets that instead — overwriting it would break anything
 * already referencing that element.
 *
 * When `children` is not a single element (a fragment, several controls) there
 * is nothing to attach an id to, so the label renders unassociated rather than
 * pointing at an id no element carries.
 */
export const Field = ({
  label,
  children,
  hint,
  error,
  id,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
  error?: string;
  id?: string;
}) => {
  // useId emits «r0» on some React versions. Those are legal in an id
  // attribute but make the element unaddressable from a CSS selector.
  const generatedId = `wf-field-${useId().replace(/[^A-Za-z0-9_-]/g, '')}`;
  const child = isValidElement<{ id?: string }>(children) ? children : null;
  const controlId = (child?.props.id ?? id ?? generatedId) as string;

  return (
    <div>
      {child ? <Label htmlFor={controlId}>{label}</Label> : <Label>{label}</Label>}
      {child ? cloneElement(child as ReactElement<{ id?: string }>, { id: controlId }) : children}
      {hint && !error && <p className="mt-1 text-xs text-zinc-500">{hint}</p>}
      {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
    </div>
  );
};
