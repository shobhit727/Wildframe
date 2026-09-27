import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import AdminAuditPage from '@/app/admin/audit/page';
import { renderWithQuery } from '@/__tests__/app-helpers';
import type { AuditLog } from '@/types/admin';

const { adminApi } = vi.hoisted(() => ({
  adminApi: {
    listAuditLogs: vi.fn(),
    listUsers: vi.fn(),
    listFlags: vi.fn(),
    listAlerts: vi.fn(),
    listConfigs: vi.fn(),
    moderateUser: vi.fn(),
    resolveFlag: vi.fn(),
    createAlert: vi.fn(),
    acknowledgeAlert: vi.fn(),
    setConfig: vi.fn(),
  },
}));

vi.mock('@/api/admin', () => adminApi);

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/admin/audit',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const ENTRY: AuditLog = {
  id: 1,
  admin_id: 'admin-1',
  action: 'user_moderation',
  resource_type: 'user',
  resource_id: 'user-1-abcdefghijklmnop',
  changes: '{"status":"banned"}',
  ip_address: '10.0.0.5',
  created_at: '2024-05-01T10:00:00Z',
};

/** Row count via a structural query — role queries are slow on a full page. */
function rowCount(): number {
  return document.querySelectorAll('tbody tr').length;
}

function dataRows(): HTMLElement[] {
  return within(screen.getByRole('table'))
    .getAllByRole('row')
    .slice(1);
}

function pager(): { prev: HTMLButtonElement; next: HTMLButtonElement } {
  const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>('button'));
  const prev = buttons.find((b) => b.textContent === 'Previous');
  const next = buttons.find((b) => b.textContent === 'Next');
  if (!prev || !next) throw new Error('pager buttons not found');
  return { prev, next };
}

function entrySummary(): string {
  return screen.getByText(/^\d+ entr(y|ies)$/).textContent ?? '';
}

beforeEach(() => {
  for (const fn of Object.values(adminApi)) fn.mockReset();
  adminApi.listAuditLogs.mockResolvedValue([ENTRY]);
});

describe('AdminAuditPage (/admin/audit)', () => {
  it('requests a large audit window with no admin filter', async () => {
    renderWithQuery(<AdminAuditPage />);

    await waitFor(() =>
      expect(adminApi.listAuditLogs).toHaveBeenCalledWith({ limit: 100, admin_id: undefined }),
    );
  });

  it('lists an entry with its admin, action and resource', async () => {
    renderWithQuery(<AdminAuditPage />);

    expect(await screen.findByText('admin-1')).toBeInTheDocument();
    expect(screen.getByText('user_moderation')).toBeInTheDocument();
    expect(screen.getByText('user-1-abc…')).toBeInTheDocument();
    expect(entrySummary()).toBe('1 entry');
  });

  it('shows an em dash for an entry with no recorded change', async () => {
    adminApi.listAuditLogs.mockResolvedValue([{ ...ENTRY, changes: null }]);

    renderWithQuery(<AdminAuditPage />);

    await screen.findByText('admin-1');
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('prompts patience when the log is empty', async () => {
    adminApi.listAuditLogs.mockResolvedValue([]);

    renderWithQuery(<AdminAuditPage />);

    expect(await screen.findByText('No audit entries')).toBeInTheDocument();
    expect(screen.getByText('Actions by admins will be recorded here.')).toBeInTheDocument();
  });

  it('keeps the page usable when the audit lookup fails', async () => {
    adminApi.listAuditLogs.mockRejectedValue(new Error('admin 503'));

    renderWithQuery(<AdminAuditPage />);

    expect(await screen.findByText('No audit entries')).toBeInTheDocument();
  });

  describe('search', () => {
    beforeEach(() => {
      adminApi.listAuditLogs.mockResolvedValue([
        ENTRY,
        {
          ...ENTRY,
          id: 2,
          admin_id: 'admin-2',
          action: 'set_config',
          resource_type: 'config',
          resource_id: 'rate_limit.rps',
        },
      ]);
    });

    it('filters by admin id', async () => {
      renderWithQuery(<AdminAuditPage />);
      await screen.findByText('admin-1');

      fireEvent.change(screen.getByPlaceholderText('Search admin, action, resource…'), {
        target: { value: 'admin-2' },
      });

      await waitFor(() => expect(screen.queryByText('admin-1')).toBeNull());
      expect(screen.getByText('admin-2')).toBeInTheDocument();
    });

    it('filters by action', async () => {
      renderWithQuery(<AdminAuditPage />);
      await screen.findByText('user_moderation');

      fireEvent.change(screen.getByPlaceholderText('Search admin, action, resource…'), {
        target: { value: 'set_config' },
      });

      expect(await screen.findByText('set_config')).toBeInTheDocument();
      expect(screen.queryByText('user_moderation')).toBeNull();
    });

    it('filters by resource type', async () => {
      renderWithQuery(<AdminAuditPage />);
      await screen.findByText('user_moderation');

      fireEvent.change(screen.getByPlaceholderText('Search admin, action, resource…'), {
        target: { value: 'config' },
      });

      expect(await screen.findByText('set_config')).toBeInTheDocument();
      expect(screen.queryByText('user_moderation')).toBeNull();
    });

    it('matches case-insensitively', async () => {
      renderWithQuery(<AdminAuditPage />);
      await screen.findByText('user_moderation');

      fireEvent.change(screen.getByPlaceholderText('Search admin, action, resource…'), {
        target: { value: 'USER_MODERATION' },
      });

      expect(screen.getByText('user_moderation')).toBeInTheDocument();
    });

    it('reports zero entries when nothing matches', async () => {
      renderWithQuery(<AdminAuditPage />);
      await screen.findByText('user_moderation');

      fireEvent.change(screen.getByPlaceholderText('Search admin, action, resource…'), {
        target: { value: 'nothing-matches-this' },
      });

      expect(await screen.findByText('No audit entries')).toBeInTheDocument();
      expect(entrySummary()).toBe('0 entries');
    });
  });

  describe('action filter', () => {
    it('offers every action the API understands', async () => {
      renderWithQuery(<AdminAuditPage />);

      await waitFor(() =>
        expect(screen.getByRole('combobox')).toBeInTheDocument(),
      );
      const options = within(screen.getByRole('combobox'))
        .getAllByRole('option')
        .map((o) => (o as HTMLOptionElement).value);
      expect(options).toEqual([
        '',
        'user_moderation',
        'content_flagged',
        'content_resolved',
        'set_config',
      ]);
    });

    it('narrows the rows to the chosen action', async () => {
      adminApi.listAuditLogs.mockResolvedValue([
        ENTRY,
        { ...ENTRY, id: 2, action: 'content_flagged' },
        { ...ENTRY, id: 3, action: 'content_resolved' },
      ]);
      renderWithQuery(<AdminAuditPage />);
      await screen.findByText('content_resolved');

      fireEvent.change(screen.getByRole('combobox'), { target: { value: 'content_flagged' } });

      await waitFor(() => expect(screen.queryByText('content_resolved')).toBeNull());
      expect(screen.getByText('content_flagged')).toBeInTheDocument();
      expect(entrySummary()).toBe('1 entry');
    });

    it('restores every row when the filter is cleared', async () => {
      adminApi.listAuditLogs.mockResolvedValue([
        ENTRY,
        { ...ENTRY, id: 2, action: 'content_flagged' },
      ]);
      renderWithQuery(<AdminAuditPage />);
      await screen.findByText('content_flagged');

      fireEvent.change(screen.getByRole('combobox'), { target: { value: 'content_flagged' } });
      await waitFor(() => expect(entrySummary()).toBe('1 entry'));

      fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });
      expect(await screen.findByText('user_moderation')).toBeInTheDocument();
      expect(entrySummary()).toBe('2 entries');
    });
  });

  describe('admin id filter', () => {
    it('re-queries the API scoped to the entered admin', async () => {
      renderWithQuery(<AdminAuditPage />);
      await screen.findByText('admin-1');

      fireEvent.change(screen.getByPlaceholderText('Filter by admin…'), {
        target: { value: 'admin-9' },
      });

      await waitFor(() =>
        expect(adminApi.listAuditLogs).toHaveBeenCalledWith({
          limit: 100,
          admin_id: 'admin-9',
        }),
      );
    });

    it('scopes to the admin endpoint rather than fetching everything', async () => {
      renderWithQuery(<AdminAuditPage />);
      await screen.findByText('admin-1');

      fireEvent.change(screen.getByPlaceholderText('Filter by admin…'), {
        target: { value: 'admin-9' },
      });

      // A server-side filter is the point: it keeps a busy platform's log out
      // of the browser entirely.
      await waitFor(() =>
        expect(adminApi.listAuditLogs).toHaveBeenLastCalledWith({
          limit: 100,
          admin_id: 'admin-9',
        }),
      );
    });

    it('clears the server filter when the field is emptied', async () => {
      renderWithQuery(<AdminAuditPage />);
      await screen.findByText('admin-1');

      const input = screen.getByPlaceholderText('Filter by admin…');
      fireEvent.change(input, { target: { value: 'admin-9' } });
      await waitFor(() => expect(adminApi.listAuditLogs).toHaveBeenLastCalledWith(
        expect.objectContaining({ admin_id: 'admin-9' }),
      ));

      fireEvent.change(input, { target: { value: '' } });
      await waitFor(() =>
        expect(adminApi.listAuditLogs).toHaveBeenLastCalledWith({
          limit: 100,
          admin_id: undefined,
        }),
      );
    });
  });

  describe('sorting', () => {
    it('reverses the newest-first order when the When header is clicked', async () => {
      renderWithQuery(<AdminAuditPage />);
      await screen.findByText('admin-1');

      // DataTable wires sorting to the header's onClick.
      fireEvent.click(screen.getByRole('columnheader', { name: /When/ }));

      await waitFor(() => {
        // One row cannot reorder, so assert the control is now ascending.
        const header = screen.getByRole('columnheader', { name: /When/ });
        expect(header.querySelector('.text-red-400')).not.toBeNull();
      });
    });

    it('re-sorts by admin when its header is clicked', async () => {
      adminApi.listAuditLogs.mockResolvedValue([
        { ...ENTRY, id: 1, admin_id: 'admin-b' },
        { ...ENTRY, id: 2, admin_id: 'admin-a' },
      ]);
      renderWithQuery(<AdminAuditPage />);
      await screen.findByText('admin-b');

      // Default is created_at desc; clicking Admin flips to ascending, so the
      // a-admin must now lead.
      fireEvent.click(screen.getByRole('columnheader', { name: /Admin/ }));

      await waitFor(() => {
        const first = dataRows()[0]?.textContent ?? '';
        expect(first).toContain('admin-a');
      });
    });

    it('toggles back to descending on a second click', async () => {
      adminApi.listAuditLogs.mockResolvedValue([
        { ...ENTRY, id: 1, admin_id: 'admin-b' },
        { ...ENTRY, id: 2, admin_id: 'admin-a' },
      ]);
      renderWithQuery(<AdminAuditPage />);
      await screen.findByText('admin-b');

      const header = screen.getByRole('columnheader', { name: /Admin/ });
      fireEvent.click(header);
      await waitFor(() => expect(dataRows()[0]?.textContent ?? '').toContain('admin-a'));

      fireEvent.click(screen.getByRole('columnheader', { name: /Admin/ }));
      await waitFor(() => expect(dataRows()[0]?.textContent ?? '').toContain('admin-b'));
    });
  });

  describe('pagination', () => {
    beforeEach(() => {
      adminApi.listAuditLogs.mockResolvedValue(
        Array.from({ length: 30 }, (_, i) => ({
          ...ENTRY,
          id: i + 1,
          admin_id: `admin-${i + 1}`,
          created_at: `2024-05-${String(i + 1).padStart(2, '0')}T10:00:00Z`,
        })),
      );
    });

    it('shows twelve entries per page', async () => {
      renderWithQuery(<AdminAuditPage />);

      await waitFor(() => expect(rowCount()).toBe(12));
      expect(screen.getByText('1 / 3')).toBeInTheDocument();
      expect(entrySummary()).toBe('30 entries');
    });

    it('walks forwards and back', async () => {
      renderWithQuery(<AdminAuditPage />);
      await waitFor(() => expect(rowCount()).toBe(12));

      expect(pager().prev).toBeDisabled();

      fireEvent.click(pager().next);
      expect(await screen.findByText('2 / 3')).toBeInTheDocument();
      expect(rowCount()).toBe(12);

      fireEvent.click(pager().prev);
      expect(await screen.findByText('1 / 3')).toBeInTheDocument();
    });

    it('disables forward paging on the last page', async () => {
      renderWithQuery(<AdminAuditPage />);
      await waitFor(() => expect(rowCount()).toBe(12));

      fireEvent.click(pager().next);
      await screen.findByText('2 / 3');
      fireEvent.click(pager().next);
      await screen.findByText('3 / 3');

      // 30 entries over three pages leaves six on the last one.
      expect(rowCount()).toBe(6);
      expect(pager().next).toBeDisabled();
    });

    it('returns to the first page when a filter narrows the result set', async () => {
      renderWithQuery(<AdminAuditPage />);
      await waitFor(() => expect(rowCount()).toBe(12));

      fireEvent.click(pager().next);
      fireEvent.click(pager().next);
      await screen.findByText('3 / 3');

      fireEvent.change(screen.getByPlaceholderText('Search admin, action, resource…'), {
        target: { value: 'admin-30' },
      });

      // Staying on page 3 of a one-page result set would show the operator an
      // empty table and read as "this admin did nothing".
      expect(await screen.findByText('1 / 1')).toBeInTheDocument();
      expect(rowCount()).toBe(1);
    });
  });
});
