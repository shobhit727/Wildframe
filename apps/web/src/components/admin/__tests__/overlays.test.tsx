/**
 * Interactive admin primitives: the two Radix dialogs and the search bar.
 * These are the pieces that gate destructive moderation actions, so the focus
 * is on who can dismiss them, what closes them, and what the buttons say.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ActionDrawer } from '@/components/admin/ActionDrawer';
import { ConfirmDialog } from '@/components/admin/ConfirmDialog';
import { FilterBar } from '@/components/admin/FilterBar';

describe('ConfirmDialog', () => {
  function renderDialog(overrides: Partial<Parameters<typeof ConfirmDialog>[0]> = {}) {
    const props = {
      open: true,
      onOpenChange: vi.fn(),
      title: 'Ban user ada@example.com?',
      onConfirm: vi.fn(),
      ...overrides,
    };
    return { props, ...render(<ConfirmDialog {...props} />) };
  }

  it('renders the title as an accessible heading', async () => {
    renderDialog();

    expect(await screen.findByRole('heading', { name: 'Ban user ada@example.com?' })).toBeInTheDocument();
  });

  it('renders the description when supplied', async () => {
    renderDialog({ description: 'They lose access immediately. This cannot be undone.' });

    expect(
      await screen.findByText('They lose access immediately. This cannot be undone.')
    ).toBeInTheDocument();
  });

  it('omits the description element entirely when not supplied', async () => {
    renderDialog();

    await screen.findByRole('heading');
    expect(screen.queryByText(/cannot be undone/)).toBeNull();
  });

  it('invokes onConfirm from the confirm button', async () => {
    const { props } = renderDialog();

    fireEvent.click(await screen.findByRole('button', { name: 'Confirm' }));

    expect(props.onConfirm).toHaveBeenCalledTimes(1);
    expect(props.onOpenChange).not.toHaveBeenCalled();
  });

  it('requests close without confirming from the cancel button', async () => {
    const { props } = renderDialog();

    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));

    expect(props.onOpenChange).toHaveBeenCalledWith(false);
    expect(props.onConfirm).not.toHaveBeenCalled();
  });

  it('uses the caller-supplied button labels', async () => {
    renderDialog({ confirmLabel: 'Ban account', cancelLabel: 'Keep account' });

    expect(await screen.findByRole('button', { name: 'Ban account' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Keep account' })).toBeInTheDocument();
  });

  it('disables the confirm button and shows progress copy while loading', async () => {
    renderDialog({ loading: true });

    const button = await screen.findByRole('button', { name: 'Working…' });
    expect(button).toBeDisabled();
  });

  it('cannot be confirmed while loading', async () => {
    const { props } = renderDialog({ loading: true });

    fireEvent.click(await screen.findByRole('button', { name: 'Working…' }));

    // A double-submit here would fire two moderation POSTs.
    expect(props.onConfirm).not.toHaveBeenCalled();
  });

  it('leaves the cancel button enabled while loading so the admin can back out', async () => {
    const { props } = renderDialog({ loading: true });

    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));

    expect(props.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('styles the confirm action as destructive by default', async () => {
    renderDialog();

    expect((await screen.findByRole('button', { name: 'Confirm' })).className).toContain('bg-red-600');
  });

  it('drops the destructive styling when told the action is safe', async () => {
    renderDialog({ destructive: false });

    expect((await screen.findByRole('button', { name: 'Confirm' })).className).toContain('bg-zinc-700');
  });

  it('closes on Escape', async () => {
    const { props } = renderDialog();
    const content = await screen.findByRole('heading');

    fireEvent.keyDown(content, { key: 'Escape' });

    await waitFor(() => expect(props.onOpenChange).toHaveBeenCalledWith(false));
  });

  it('renders nothing when closed', () => {
    const { container } = renderDialog({ open: false });

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('exposes itself as a modal dialog', async () => {
    renderDialog();

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });
});

describe('ActionDrawer', () => {
  function renderDrawer(overrides: Partial<Parameters<typeof ActionDrawer>[0]> = {}) {
    const props = {
      open: true,
      onOpenChange: vi.fn(),
      title: 'Moderate user',
      children: <p>Pick a status</p>,
      ...overrides,
    };
    return { props, ...render(<ActionDrawer {...props} />) };
  }

  it('renders the title and body content', async () => {
    renderDrawer();

    expect(await screen.findByRole('heading', { name: 'Moderate user' })).toBeInTheDocument();
    expect(screen.getByText('Pick a status')).toBeInTheDocument();
  });

  it('renders the description when supplied', async () => {
    renderDrawer({ description: 'Changes are written to the audit log.' });

    expect(await screen.findByText('Changes are written to the audit log.')).toBeInTheDocument();
  });

  it('closes when the close button is pressed', async () => {
    const { props } = renderDrawer();

    fireEvent.click(await screen.findByRole('button', { name: 'Close' }));

    expect(props.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('closes on Escape', async () => {
    const { props } = renderDrawer();
    const heading = await screen.findByRole('heading');

    fireEvent.keyDown(heading, { key: 'Escape' });

    await waitFor(() => expect(props.onOpenChange).toHaveBeenCalledWith(false));
  });

  it('renders the footer slot when supplied', async () => {
    renderDrawer({ footer: <button type="button">Save changes</button> });

    expect(await screen.findByRole('button', { name: 'Save changes' })).toBeInTheDocument();
  });

  it('omits the footer container when no footer is given', async () => {
    renderDrawer({ footer: undefined });

    await screen.findByRole('heading');
    // The footer wrapper is the only element with a top border in the drawer.
    expect(document.querySelector('[role="dialog"] .border-t')).toBeNull();
  });

  it('draws the footer separator when a footer is given', async () => {
    renderDrawer({ footer: <button type="button">Save</button> });

    await screen.findByRole('heading');
    expect(document.querySelector('[role="dialog"] .border-t')).not.toBeNull();
  });

  it('applies the width for each size variant', async () => {
    const { rerender } = renderDrawer({ size: 'sm' });
    // The dialog is portalled to document.body, not the render container.
    await screen.findByRole('heading');
    expect(document.querySelector('[role="dialog"]')?.className).toContain('max-w-md');

    rerender(
      <ActionDrawer open onOpenChange={vi.fn()} title="Moderate user" size="lg">
        <p>Pick a status</p>
      </ActionDrawer>
    );
    await waitFor(() =>
      expect(document.querySelector('[role="dialog"]')?.className).toContain('max-w-3xl')
    );
  });

  it('defaults to the medium width', async () => {
    renderDrawer();

    await screen.findByRole('heading');
    expect(document.querySelector('[role="dialog"]')?.className).toContain('max-w-xl');
  });

  it('renders nothing when closed', () => {
    const { container } = renderDrawer({ open: false });

    expect(container).toBeEmptyDOMElement();
  });
});

describe('FilterBar', () => {
  it('reports every keystroke with the full new value', () => {
    const onChange = vi.fn();
    render(<FilterBar value="" onChange={onChange} placeholder="Search users" />);

    const input = screen.getByPlaceholderText('Search users');
    fireEvent.change(input, { target: { value: 'ad' } });

    expect(onChange).toHaveBeenCalledWith('ad');
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it('is controlled — the input shows the value prop, not its own state', () => {
    render(<FilterBar value="pinned query" onChange={vi.fn()} />);

    // Without a parent that echoes the value back, typing must not appear to
    // work: the box would keep showing the old query.
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('pinned query');
  });

  it('clears correctly when the parent empties the value', () => {
    const { rerender } = render(<FilterBar value="abc" onChange={vi.fn()} />);
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('abc');

    rerender(<FilterBar value="" onChange={vi.fn()} />);
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('');
  });

  it('falls back to an ellipsis placeholder', () => {
    render(<FilterBar value="" onChange={vi.fn()} />);
    expect(screen.getByPlaceholderText('Search…')).toBeInTheDocument();
  });

  it('exposes a textbox that accepts free text', () => {
    render(<FilterBar value="" onChange={vi.fn()} />);
    const input = screen.getByRole('textbox') as HTMLInputElement;
    expect(input.type).toBe('text');
    expect(input).not.toBeDisabled();
  });

  it('has no accessible name, so the search box is unlabelled for screen readers', () => {
    // BUG (FilterBar.tsx:28-37): the only affordance is a placeholder, which
    // is not an accessible name. VoiceOver/NVDA announce "edit text, blank"
    // and the admin has no way to know the field filters the table.
    render(<FilterBar value="" onChange={vi.fn()} placeholder="Search users" />);

    const input = screen.getByRole('textbox');
    const labelled = screen.queryByLabelText('Search users');
    expect(labelled).toBeNull();
    expect(input).toHaveAttribute('placeholder', 'Search users');
  });

  it('renders the left slot next to the search box', () => {
    render(<FilterBar value="" onChange={vi.fn()} left={<select aria-label="Status"><option>All</option></select>} />);

    expect(screen.getByRole('combobox', { name: 'Status' })).toBeInTheDocument();
  });

  it('renders the right slot', () => {
    render(<FilterBar value="" onChange={vi.fn()} right={<button type="button">Reset</button>} />);

    expect(screen.getByRole('button', { name: 'Reset' })).toBeInTheDocument();
  });

  it('keeps both slots and the input in one bar', () => {
    const { container } = render(
      <FilterBar
        value=""
        onChange={vi.fn()}
        left={<span>left</span>}
        right={<span>right</span>}
      />
    );

    const bar = container.firstElementChild as HTMLElement;
    expect(bar).toContainElement(screen.getByRole('textbox'));
    expect(bar).toContainElement(screen.getByText('left'));
    expect(bar).toContainElement(screen.getByText('right'));
  });
});
