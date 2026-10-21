import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import AdminFlagsPage from '@/app/admin/flags/page';
import { renderWithQuery } from '@/__tests__/app-helpers';
import type { ContentFlag } from '@/types/admin';

const { adminApi } = vi.hoisted(() => ({
  adminApi: {
    listFlags: vi.fn(),
    resolveFlag: vi.fn(),
    listUsers: vi.fn(),
    listAlerts: vi.fn(),
    listConfigs: vi.fn(),
    listAuditLogs: vi.fn(),
    moderateUser: vi.fn(),
    createAlert: vi.fn(),
    acknowledgeAlert: vi.fn(),
    setConfig: vi.fn(),
  },
}));

vi.mock('@/api/admin', () => adminApi);

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/admin/flags',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const FLAG: ContentFlag = {
  id: 1,
  content_id: 'content-abcdefghijklmnop',
  content_type: 'movie',
  status: 'flagged',
  reason: 'explicit violence',
  flagged_at: '2024-05-01T10:00:00Z',
  resolved_at: null,
  created_at: '2024-05-01T10:00:00Z',
};

function makeFlags(count: number): ContentFlag[] {
  return Array.from({ length: count }, (_, i) => ({
    ...FLAG,
    id: i + 1,
    content_id: `content-${i + 1}-abcdefghijklmnop`,
    reason: `reason ${i + 1}`,
    flagged_at: `2024-05-${String(i + 1).padStart(2, '0')}T10:00:00Z`,
  }));
}

function dataRows(): HTMLElement[] {
  return within(screen.getByRole('table'))
    .getAllByRole('row')
    .slice(1);
}

/**
 * Row count via a structural query. Accessible role queries cost roughly half
 * a second each on a full ten-row moderation table, which is enough to blow
 * the test timeout once a test makes several of them.
 */
function rowCount(): number {
  return document.querySelectorAll('tbody tr').length;
}

/** Both pager buttons from a single role query pass. */
function pager(): { prev: HTMLButtonElement; next: HTMLButtonElement } {
  const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>('button'));
  const prev = buttons.find((b) => b.textContent === 'Previous');
  const next = buttons.find((b) => b.textContent === 'Next');
  if (!prev || !next) throw new Error('pager buttons not found');
  return { prev, next };
}

function countText(): string {
  return screen.getByText(/^\d+ flags?$/).textContent ?? '';
}

/** Confirm the visible dialog's action button. */
async function confirmDialog(action: string): Promise<void> {
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: action }));
}

beforeEach(() => {
  for (const fn of Object.values(adminApi)) fn.mockReset();
  adminApi.listFlags.mockResolvedValue([FLAG]);
  adminApi.resolveFlag.mockResolvedValue({});
});

describe('AdminFlagsPage (/admin/flags)', () => {
  it('requests the open flag queue', async () => {
    renderWithQuery(<AdminFlagsPage />);

    await waitFor(() => expect(adminApi.listFlags).toHaveBeenCalledWith({ limit: 100 }));
  });

  it('lists a flagged title with its reason and status', async () => {
    renderWithQuery(<AdminFlagsPage />);

    expect(await screen.findByText('content-abcdefgh…')).toBeInTheDocument();
    expect(screen.getByText('explicit violence')).toBeInTheDocument();
    expect(screen.getByText('flagged')).toBeInTheDocument();
    expect(screen.getByText('movie')).toBeInTheDocument();
  });

  it('shows an em dash for a flag with no reason', async () => {
    adminApi.listFlags.mockResolvedValue([{ ...FLAG, reason: null }]);

    renderWithQuery(<AdminFlagsPage />);

    await screen.findByText('content-abcdefgh…');
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('reports a clear queue when nothing is flagged', async () => {
    adminApi.listFlags.mockResolvedValue([]);

    renderWithQuery(<AdminFlagsPage />);

    expect(await screen.findByText('No open flags')).toBeInTheDocument();
    expect(screen.getByText('The moderation queue is clear.')).toBeInTheDocument();
  });

  it('keeps the queue usable when the flag lookup fails', async () => {
    adminApi.listFlags.mockRejectedValue(new Error('admin 503'));

    renderWithQuery(<AdminFlagsPage />);

    expect(await screen.findByText('No open flags')).toBeInTheDocument();
  });

  describe('search', () => {
    beforeEach(() => {
      adminApi.listFlags.mockResolvedValue([
        FLAG,
        { ...FLAG, id: 2, content_id: 'other-zzzz', content_type: 'show', reason: 'spam' },
      ]);
    });

    it('filters by content id', async () => {
      renderWithQuery(<AdminFlagsPage />);
      await screen.findByText('content-abcdefgh…');

      fireEvent.change(
        screen.getByPlaceholderText('Search by content ID, type, or reason…'),
        { target: { value: 'other-' } },
      );

      await waitFor(() => expect(screen.queryByText('content-abcdefgh…')).toBeNull());
      expect(screen.getByText('other-zzzz…')).toBeInTheDocument();
    });

    it('filters by reason', async () => {
      renderWithQuery(<AdminFlagsPage />);
      await screen.findByText('explicit violence');

      fireEvent.change(
        screen.getByPlaceholderText('Search by content ID, type, or reason…'),
        { target: { value: 'spam' } },
      );

      expect(await screen.findByText('spam')).toBeInTheDocument();
      expect(screen.queryByText('explicit violence')).toBeNull();
    });

    it('matches case-insensitively', async () => {
      renderWithQuery(<AdminFlagsPage />);
      await screen.findByText('explicit violence');

      fireEvent.change(
        screen.getByPlaceholderText('Search by content ID, type, or reason…'),
        { target: { value: 'VIOLENCE' } },
      );

      expect(screen.getByText('explicit violence')).toBeInTheDocument();
    });

    it('shows the clear-queue state when nothing matches', async () => {
      renderWithQuery(<AdminFlagsPage />);
      await screen.findByText('explicit violence');

      fireEvent.change(
        screen.getByPlaceholderText('Search by content ID, type, or reason…'),
        { target: { value: 'qqq-no-such-flag' } },
      );

      expect(await screen.findByText('No open flags')).toBeInTheDocument();
      expect(countText()).toBe('0 flags');
    });
  });

  describe('pagination', () => {
    beforeEach(() => {
      adminApi.listFlags.mockResolvedValue(makeFlags(25));
    });

    it('shows ten flags per page with a page counter', async () => {
      renderWithQuery(<AdminFlagsPage />);

      await waitFor(() => expect(rowCount()).toBe(10));
      expect(screen.getByText('1 / 3')).toBeInTheDocument();
      expect(countText()).toBe('25 flags');
    });

    it('advances and rewinds through the pages', async () => {
      renderWithQuery(<AdminFlagsPage />);
      await waitFor(() => expect(rowCount()).toBe(10));

      const { prev, next } = pager();
      expect(prev).toBeDisabled();

      fireEvent.click(next);
      expect(await screen.findByText('2 / 3')).toBeInTheDocument();

      fireEvent.click(pager().prev);
      expect(await screen.findByText('1 / 3')).toBeInTheDocument();
    });

    it('disables the next control on the final page', async () => {
      renderWithQuery(<AdminFlagsPage />);
      await waitFor(() => expect(rowCount()).toBe(10));

      fireEvent.click(pager().next);
      await screen.findByText('2 / 3');
      fireEvent.click(pager().next);
      await screen.findByText('3 / 3');

      // 25 flags over three pages leaves five on the last one, and there is
      // nowhere further to go.
      expect(rowCount()).toBe(5);
      expect(pager().next).toBeDisabled();
      expect(pager().prev).toBeEnabled();
    });
  });

  describe('resolve', () => {
    it('asks for confirmation naming the content and target status', async () => {
      renderWithQuery(<AdminFlagsPage />);
      await screen.findByText('content-abcdefgh…');

      fireEvent.click(screen.getByRole('button', { name: /Resolve/ }));

      const dialog = await screen.findByRole('dialog', { name: 'Resolve flag' });
      expect(
        within(dialog).getByText(/Content content-abcdefghijklmnop \(type: movie\) will be set to "active"/),
      ).toBeInTheDocument();
      expect(adminApi.resolveFlag).not.toHaveBeenCalled();
    });

    it('resolves to active once confirmed', async () => {
      renderWithQuery(<AdminFlagsPage />);
      await screen.findByText('content-abcdefgh…');

      fireEvent.click(screen.getByRole('button', { name: /Resolve/ }));
      await confirmDialog('Resolve');

      await waitFor(() =>
        expect(adminApi.resolveFlag).toHaveBeenCalledWith('content-abcdefghijklmnop', 'active'),
      );
    });

    it('dismisses to removed and warns it also removes the content', async () => {
      renderWithQuery(<AdminFlagsPage />);
      await screen.findByText('content-abcdefgh…');

      fireEvent.click(screen.getByRole('button', { name: /Dismiss/ }));

      const dialog = await screen.findByRole('dialog', {
        name: 'Dismiss flag & remove content',
      });
      // "removed" is destructive: it deletes the title, not just the flag.
      expect(within(dialog).getByText(/will be set to "removed"/)).toBeInTheDocument();

      fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
      await waitFor(() =>
        expect(adminApi.resolveFlag).toHaveBeenCalledWith('content-abcdefghijklmnop', 'removed'),
      );
    });

    it('changes nothing when the dialog is dismissed', async () => {
      renderWithQuery(<AdminFlagsPage />);
      await screen.findByText('content-abcdefgh…');

      fireEvent.click(screen.getByRole('button', { name: /Resolve/ }));
      await confirmDialog('Cancel');

      expect(adminApi.resolveFlag).not.toHaveBeenCalled();
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    });

    it('keeps the flag listed when resolution fails', async () => {
      adminApi.resolveFlag.mockRejectedValue({ response: { data: { detail: 'content locked' } } });

      renderWithQuery(<AdminFlagsPage />);
      await screen.findByText('content-abcdefgh…');

      fireEvent.click(screen.getByRole('button', { name: /Resolve/ }));
      await confirmDialog('Resolve');

      await waitFor(() => expect(adminApi.resolveFlag).toHaveBeenCalled());
      // The dialog only closes on success, so the operator can see it failed.
      expect(screen.getByText('content-abcdefgh…')).toBeInTheDocument();
    });
  });

  describe('sorting', () => {
    beforeEach(() => {
      adminApi.listFlags.mockResolvedValue([
        { ...FLAG, id: 1, content_id: 'old-1111', flagged_at: '2024-01-01T00:00:00Z' },
        { ...FLAG, id: 2, content_id: 'new-2222', flagged_at: '2024-09-09T00:00:00Z' },
      ]);
    });

    it('lists the newest flag first by default', async () => {
      renderWithQuery(<AdminFlagsPage />);

      await screen.findByText('new-2222…');
      const order = dataRows().map((row) => row.textContent ?? '');
      expect(order[0]).toContain('new-2222');
    });

    it('reverses the order when the status column is clicked', async () => {
      renderWithQuery(<AdminFlagsPage />);
      await screen.findByText('new-2222…');

      // DataTable wires sorting through the column header's onClick.
      fireEvent.click(screen.getByRole('columnheader', { name: /Status/ }));

      await waitFor(() => {
        const order = dataRows().map((row) => row.textContent ?? '');
        expect(order[0]).toContain('old-1111');
      });
    });
  });
});
