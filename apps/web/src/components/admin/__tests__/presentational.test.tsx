/**
 * Presentational admin primitives: KPI tiles, status pills, empty states,
 * loading skeletons, the button wrapper and the form fields.
 *
 * These carry meaning through colour and text (a "critical" alert must not
 * read as "info"), so the assertions target the rendered copy and the tone
 * mapping, not snapshots.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { AdminButton } from '@/components/admin/Button';
import { EmptyState } from '@/components/admin/EmptyState';
import { Field, Input, Label, Select, Textarea } from '@/components/admin/fields';
import { Skeleton, StatCardSkeleton, TableRowSkeleton } from '@/components/admin/Skeleton';
import { StatCard } from '@/components/admin/StatCard';
import { StatusBadge, statusTone } from '@/components/admin/StatusBadge';

describe('StatCard', () => {
  it('shows the label and the value', () => {
    render(<StatCard label="Total users" value="12,480" />);

    expect(screen.getByText('Total users')).toBeInTheDocument();
    expect(screen.getByText('12,480')).toBeInTheDocument();
  });

  it('renders a non-string value such as a formatted number', () => {
    render(<StatCard label="Active alerts" value={7} />);
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('renders a positive trend with an up arrow and no minus sign', () => {
    render(<StatCard label="Active users" value="90" trend={{ value: 12, positive: true }} />);

    const trend = screen.getByText(/12%/);
    expect(trend.textContent).toContain('↑');
    expect(trend.textContent).not.toContain('-');
  });

  it('renders a negative trend with a down arrow and the magnitude', () => {
    render(<StatCard label="Suspended" value="4" trend={{ value: -8 }} />);

    const trend = screen.getByText(/8%/);
    expect(trend.textContent).toContain('↓');
    // A "-8%" reading would double-negate against the down arrow.
    expect(trend.textContent).not.toContain('-8');
  });

  it('treats a trend with no explicit direction as negative', () => {
    render(<StatCard label="Flagged" value="3" trend={{ value: 5 }} />);
    expect(screen.getByText(/5%/).textContent).toContain('↓');
  });

  it('shows no trend chip when the trend is omitted', () => {
    render(<StatCard label="Uptime" value="72h" />);
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it('renders the hint text', () => {
    render(<StatCard label="Uptime" value="72h" hint="since last restart" />);
    expect(screen.getByText('since last restart')).toBeInTheDocument();
  });

  it('renders the icon slot', () => {
    render(<StatCard label="MRR" value="$4.2k" icon={<span>chart</span>} />);
    expect(screen.getByText('chart')).toBeInTheDocument();
  });

  it('omits the icon wrapper when no icon is given', () => {
    const { container } = render(<StatCard label="MRR" value="$4.2k" />);
    expect(screen.queryByText('chart')).toBeNull();
    expect(container.querySelectorAll('.rounded-lg.bg-white\\/5')).toHaveLength(0);
  });

  it('gives the trend chip the positive or negative colour treatment', () => {
    const { container, rerender } = render(
      <StatCard label="x" value="1" trend={{ value: 3, positive: true }} />
    );
    expect(container.querySelector('.bg-green-500\\/15')).not.toBeNull();

    rerender(<StatCard label="x" value="1" trend={{ value: 3, positive: false }} />);
    expect(container.querySelector('.bg-red-500\\/15')).not.toBeNull();
  });
});

describe('statusTone', () => {
  it.each([
    ['active', 'green'],
    ['suspended', 'amber'],
    ['banned', 'red'],
    ['flagged', 'amber'],
    ['removed', 'red'],
    ['info', 'sky'],
    ['warning', 'amber'],
    ['critical', 'red'],
    ['movie', 'purple'],
    ['show', 'sky'],
    ['episode', 'zinc'],
  ])('maps %s to the %s tone', (status, tone) => {
    expect(statusTone(status)).toBe(tone);
  });

  it('falls back to the neutral tone for an unrecognised status', () => {
    expect(statusTone('archived')).toBe('zinc');
  });

  it('is case sensitive, so an upper-cased status renders neutral', () => {
    // BUG (StatusBadge.tsx:18-33): STATUS_TONE keys are all lower case and the
    // lookup is exact. The admin pages build statuses from API payloads; a
    // backend that returns "ACTIVE" (or a `.toUpperCase()`d filter value) would
    // silently render a banned user in the neutral tone.
    expect(statusTone('ACTIVE')).toBe('zinc');
    expect(statusTone('active')).toBe('green');
  });
});

describe('StatusBadge', () => {
  it('renders the raw status text', () => {
    render(<StatusBadge status="suspended" />);
    expect(screen.getByText('suspended')).toBeInTheDocument();
  });

  it('applies the tone derived from the status', () => {
    const { container } = render(<StatusBadge status="banned" />);
    expect(container.querySelector('.bg-red-500\\/15')).not.toBeNull();
  });

  it('lets the caller override the derived tone', () => {
    const { container } = render(<StatusBadge status="banned" tone="sky" />);
    expect(container.querySelector('.bg-sky-500\\/15')).not.toBeNull();
    expect(container.querySelector('.bg-red-500\\/15')).toBeNull();
  });

  it('appends the extra class name', () => {
    const { container } = render(<StatusBadge status="active" className="mr-2" />);
    expect(container.firstElementChild?.className).toContain('mr-2');
  });

  it('colours the dot to match the badge tone', () => {
    const { container } = render(<StatusBadge status="critical" />);
    expect(container.querySelector('.bg-red-400')).not.toBeNull();
  });
});

describe('EmptyState', () => {
  it('renders the title as a heading', () => {
    render(<EmptyState title="No flagged content" />);
    expect(screen.getByRole('heading', { name: 'No flagged content' })).toBeInTheDocument();
  });

  it('renders the description', () => {
    render(<EmptyState title="No alerts" description="Alerts appear when a service degrades." />);
    expect(screen.getByText('Alerts appear when a service degrades.')).toBeInTheDocument();
  });

  it('omits the description when not supplied', () => {
    render(<EmptyState title="No alerts" />);
    expect(screen.queryByRole('paragraph')).toBeNull();
  });

  it('renders the action button', () => {
    render(<EmptyState title="No users" action={<button type="button">Invite admin</button>} />);

    const button = screen.getByRole('button', { name: 'Invite admin' });
    fireEvent.click(button);
    expect(button).toBeInTheDocument();
  });

  it('renders the icon slot', () => {
    render(<EmptyState title="Nothing" icon={<span data-testid="icon" />} />);
    expect(screen.getByTestId('icon')).toBeInTheDocument();
  });
});

describe('loading skeletons', () => {
  it('applies a caller class name so callers can size the block', () => {
    const { container } = render(<Skeleton className="h-4 w-24" />);
    expect(container.firstElementChild?.className).toContain('h-4');
    expect(container.firstElementChild?.className).toContain('w-24');
  });

  it('merges a caller style over the default', () => {
    const { container } = render(<Skeleton style={{ width: 120 }} />);
    expect((container.firstElementChild as HTMLElement).style.width).toBe('120px');
  });

  it('renders three placeholder bars in the stat card skeleton', () => {
    const { container } = render(<StatCardSkeleton />);
    expect(container.querySelectorAll('.animate-pulse')).toHaveLength(3);
  });

  it('renders exactly rows x cols placeholders in the table skeleton', () => {
    const { container } = render(<TableRowSkeleton cols={4} rows={3} />);

    expect(container.querySelectorAll('.animate-pulse')).toHaveLength(12);
    expect(container.querySelectorAll('[style*="repeat(4"]')).toHaveLength(3);
  });

  it('defaults the table skeleton to a 5-column, 6-row grid', () => {
    const { container } = render(<TableRowSkeleton />);
    expect(container.querySelectorAll('.animate-pulse')).toHaveLength(30);
  });

  it('renders nothing but the container for a zero-row skeleton', () => {
    const { container } = render(<TableRowSkeleton rows={0} />);
    expect(container.querySelectorAll('.animate-pulse')).toHaveLength(0);
  });
});

describe('AdminButton', () => {
  it('renders its children as a button', () => {
    render(<AdminButton>Save config</AdminButton>);
    expect(screen.getByRole('button', { name: 'Save config' })).toBeInTheDocument();
  });

  it('fires onClick', () => {
    const onClick = vi.fn();
    render(<AdminButton onClick={onClick}>Ban user</AdminButton>);

    fireEvent.click(screen.getByRole('button', { name: 'Ban user' }));

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('does not fire onClick while disabled', () => {
    const onClick = vi.fn();
    render(
      <AdminButton disabled onClick={onClick}>
        Ban user
      </AdminButton>
    );

    const button = screen.getByRole('button', { name: 'Ban user' });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('uses the primary variant by default', () => {
    render(<AdminButton>x</AdminButton>);
    expect(screen.getByRole('button').className).toContain('bg-red-600');
  });

  it('applies the ghost variant', () => {
    render(<AdminButton variant="ghost">x</AdminButton>);
    expect(screen.getByRole('button').className).toContain('text-zinc-300');
  });

  it('applies the requested size', () => {
    render(<AdminButton size="lg">x</AdminButton>);
    expect(screen.getByRole('button').className).toContain('px-5');
  });

  it('merges a caller class name', () => {
    render(<AdminButton className="w-full">x</AdminButton>);
    expect(screen.getByRole('button').className).toContain('w-full');
  });

  it('forwards a ref to the underlying button element', () => {
    const ref = createRef<HTMLButtonElement>();
    render(<AdminButton ref={ref}>x</AdminButton>);

    expect(ref.current).toBe(screen.getByRole('button'));
    expect(ref.current?.tagName).toBe('BUTTON');
  });
});

describe('form fields', () => {
  it('associates a standalone label with its control via htmlFor', () => {
    render(
      <>
        <Label htmlFor="reason">Reason</Label>
        <Input id="reason" />
      </>
    );

    expect(screen.getByLabelText('Reason')).toBe(screen.getByRole('textbox'));
  });

  it('forwards input props including type and value', () => {
    render(<Input type="email" defaultValue="ada@example.com" />);
    expect((screen.getByRole('textbox') as HTMLInputElement).type).toBe('email');
    expect(screen.getByRole('textbox')).toHaveValue('ada@example.com');
  });

  it('reports typing on the input', () => {
    const onChange = vi.fn();
    render(<Input onChange={onChange} />);

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'spam' } });

    expect(onChange).toHaveBeenCalled();
  });

  it('renders a textarea and keeps it multiline', () => {
    render(<Textarea rows={3} />);
    const el = screen.getByRole('textbox');
    expect(el.tagName).toBe('TEXTAREA');
    expect(el).toHaveAttribute('rows', '3');
  });

  it('renders a select with its options', () => {
    render(
      <Select aria-label="Severity" defaultValue="info">
        <option value="info">Info</option>
        <option value="critical">Critical</option>
      </Select>
    );

    const select = screen.getByRole('combobox', { name: 'Severity' }) as HTMLSelectElement;
    expect(select).toHaveValue('info');
    expect(Array.from(select.options, (o) => o.value)).toEqual(['info', 'critical']);
  });

  it('forwards a ref for each control', () => {
    const inputRef = createRef<HTMLInputElement>();
    const areaRef = createRef<HTMLTextAreaElement>();
    const selectRef = createRef<HTMLSelectElement>();
    render(
      <>
        <Input ref={inputRef} />
        <Textarea ref={areaRef} />
        <Select ref={selectRef} aria-label="s">
          <option>a</option>
        </Select>
      </>
    );

    expect(inputRef.current?.tagName).toBe('INPUT');
    expect(areaRef.current?.tagName).toBe('TEXTAREA');
    expect(selectRef.current?.tagName).toBe('SELECT');
  });

  it('renders the field label text', () => {
    render(
      <Field label="Moderation reason">
        <Input />
      </Field>
    );
    expect(screen.getByText('Moderation reason')).toBeInTheDocument();
  });

  it('shows the hint when there is no error', () => {
    render(
      <Field label="Reason" hint="Shown to the user in their email">
        <Input />
      </Field>
    );
    expect(screen.getByText('Shown to the user in their email')).toBeInTheDocument();
  });

  it('replaces the hint with the error when both are supplied', () => {
    render(
      <Field label="Reason" hint="Shown to the user" error="Reason is required">
        <Input />
      </Field>
    );

    expect(screen.getByText('Reason is required')).toBeInTheDocument();
    expect(screen.queryByText('Shown to the user')).toBeNull();
  });

  it('omits both when neither hint nor error is supplied', () => {
    render(
      <Field label="Reason">
        <Input />
      </Field>
    );
    expect(screen.getAllByText('Reason')).toHaveLength(1);
  });

  it('renders a field label that is not tied to its control', () => {
    // BUG (fields.tsx:52-57): `Field` renders `<Label>` with no htmlFor, so the
    // visible "Moderation reason" text is not programmatically associated with
    // the input. Screen readers announce the field unlabelled and clicking the
    // label does not focus the input. Passing an `id` to the child and reading
    // it back through getByLabelText is the fix.
    render(
      <Field label="Moderation reason">
        <Input id="mod-reason" />
      </Field>
    );

    expect(screen.getByText('Moderation reason')).toBeInTheDocument();
    expect(screen.queryByLabelText('Moderation reason')).toBeNull();
    expect(document.getElementById('mod-reason')).toBe(screen.getByRole('textbox'));
  });
});
