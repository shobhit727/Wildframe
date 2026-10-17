import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import AdminUsersPage from '@/app/admin/users/page';
import { renderWithQuery } from '@/__tests__/app-helpers';
import type { AdminUser } from '@/types/admin';

const { adminApi } = vi.hoisted(() => ({
  adminApi: {
    listUsers: vi.fn(),
    moderateUser: vi.fn(),
    listFlags: vi.fn(),
    listAlerts: vi.fn(),
    listConfigs: vi.fn(),
    listAuditLogs: vi.fn(),
    resolveFlag: vi.fn(),
    createAlert: vi.fn(),
    acknowledgeAlert: vi.fn(),
    setConfig: vi.fn(),
  },
}));

vi.mock('@/api/admin', () => adminApi);

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/admin/users',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const ADA: AdminUser = {
  id: 'row-1',
  user_id: 'user-1-abcdefghijklmnop',
  email: 'user1@wildframe.test',
  first_name: 'First1',
  last_name: 'Last1',
  status: 'active',
  created_at: '2024-05-01T00:00:00Z',
  updated_at: '2024-05-01T00:00:00Z',
};

/**
 * Builds `count` users whose created_at increases with the index, so the page's
 * default `created_at desc` sort puts the highest index first.
 */
function makeUsers(count: number): AdminUser[] {
  return Array.from({ length: count }, (_, i) => ({
    ...ADA,
    id: `row-${i + 1}`,
    user_id: `user-${i + 1}-abcdefghijklmnop`,
    email: `user${i + 1}@wildframe.test`,
    first_name: `First${i + 1}`,
    last_name: `Last${i + 1}`,
    created_at: `2024-05-${String(i + 1).padStart(2, '0')}T00:00:00Z`,
  }));
}

/**
 * Row and menu elements are located structurally. dom-testing-library's
 * getAllByRole builds the accessibility tree for the whole document before
 * filtering, which is ~600ms per call on a full ten-row table — enough to trip
 * the test timeout once the suite runs in parallel. Radix gives these elements
 * stable roles and an aria-label, so the DOM selectors below are equivalent.
 */
function rowMenuTriggers(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('button[aria-label="Row actions"]'));
}

/**
 * Open a row's action menu. The trigger only responds to pointerdown (which
 * jsdom cannot synthesise — it has no PointerEvent) and to Enter/Space/
 * ArrowDown, so keyboard activation is the reliable path here.
 */
function openRowMenu(rowIndex = 0) {
  const triggers = rowMenuTriggers();
  if (!triggers[rowIndex]) throw new Error('row menu trigger not found');
  fireEvent.keyDown(triggers[rowIndex], { key: 'Enter' });
}

function findMenuItem(name: RegExp): HTMLElement | undefined {
  return Array.from(document.querySelectorAll<HTMLElement>('[role="menuitem"]')).find((el) =>
    name.test(el.textContent ?? ''),
  );
}

/** Select an item from the open menu (MenuItem selects on Enter). */
async function chooseMenuItem(name: RegExp) {
  let item = findMenuItem(name);
  for (let i = 0; i < 50 && !item; i += 1) {
    await waitFor(() => {});
    item = findMenuItem(name);
  }
  if (!item) throw new Error(`menu item ${name} not found`);
  fireEvent.keyDown(item, { key: 'Enter' });
}

function dataRows(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('tbody tr'));
}

/** Pager buttons located by their text, avoiding a full role-tree pass. */
function pagerButton(label: 'Previous' | 'Next'): HTMLButtonElement {
  const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>('button'));
  const found = buttons.find((b) => b.textContent === label);
  if (!found) throw new Error(`${label} button not found`);
  return found;
}

function countText(): string {
  return screen.getByText(/^\d+ users?$/).textContent ?? '';
}

beforeEach(() => {
  for (const fn of Object.values(adminApi)) fn.mockReset();
  adminApi.listUsers.mockResolvedValue([ADA]);
  adminApi.moderateUser.mockResolvedValue({});
});

describe('AdminUsersPage (/admin/users)', () => {
  it('asks the service for a full page of users', async () => {
    renderWithQuery(<AdminUsersPage />);

    await waitFor(() =>
      expect(adminApi.listUsers).toHaveBeenCalledWith({
        limit: 100,
        offset: 0,
        status: '',
        search: '',
      }),
    );
  });

  it('lists a user with their email, truncated id and status', async () => {
    renderWithQuery(<AdminUsersPage />);

    expect(await screen.findByText('First1 Last1')).toBeInTheDocument();
    expect(screen.getByText('user1@wildframe.test')).toBeInTheDocument();
    // The table truncates the id to 12 characters for scanability.
    expect(screen.getByText('user-1-abcde…')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(dataRows()).toHaveLength(1);
  });

  it('shows an empty state when there are no users', async () => {
    adminApi.listUsers.mockResolvedValue([]);

    renderWithQuery(<AdminUsersPage />);

    expect(await screen.findByText('No users found')).toBeInTheDocument();
    expect(screen.getByText('Try adjusting your search or filters.')).toBeInTheDocument();
  });

  it('keeps the table usable when the user lookup fails', async () => {
    adminApi.listUsers.mockRejectedValue(new Error('admin 503'));

    renderWithQuery(<AdminUsersPage />);

    // A failed load must read as "no rows" rather than crashing the console.
    expect(await screen.findByText('No users found')).toBeInTheDocument();
    expect(countText()).toBe('0 users');
  });

  describe('status filter', () => {
    it('narrows the request to the chosen status', async () => {
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');

      fireEvent.click(screen.getByRole('button', { name: 'Banned' }));

      await waitFor(() =>
        expect(adminApi.listUsers).toHaveBeenCalledWith(
          expect.objectContaining({ status: 'banned' }),
        ),
      );
    });

    it('offers every moderation state', async () => {
      renderWithQuery(<AdminUsersPage />);

      await screen.findByRole('heading', { name: 'Users' });
      for (const label of ['All', 'Active', 'Suspended', 'Banned']) {
        expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
      }
    });
  });

  describe('search', () => {
    it('filters the visible rows by email', async () => {
      adminApi.listUsers.mockResolvedValue([ADA, { ...ADA, id: 'row-2', user_id: 'user-2-abcdefghijklmnop', email: 'user2@wildframe.test', first_name: 'First2', last_name: 'Last2' }]);
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');

      fireEvent.change(screen.getByPlaceholderText('Search by name, email, or ID…'), {
        target: { value: 'user2' },
      });

      await waitFor(() => expect(screen.queryByText('First1 Last1')).toBeNull());
      expect(screen.getByText('First2 Last2')).toBeInTheDocument();
    });

    it('refetches on every keystroke even though the API ignores the term', async () => {
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');
      const before = adminApi.listUsers.mock.calls.length;

      const input = screen.getByPlaceholderText('Search by name, email, or ID…');
      fireEvent.change(input, { target: { value: 'u' } });
      fireEvent.change(input, { target: { value: 'us' } });

      // Reported as a bug: `search` is part of the query key but listUsers()
      // never sends it to the backend (the filter is client-side), so each
      // keystroke triggers a redundant full-table request.
      await waitFor(() =>
        expect(adminApi.listUsers.mock.calls.length).toBeGreaterThan(before + 1),
      );
    });

    it('reports zero users when nothing matches', async () => {
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');

      fireEvent.change(screen.getByPlaceholderText('Search by name, email, or ID…'), {
        target: { value: 'zzz-nobody' },
      });

      expect(await screen.findByText('No users found')).toBeInTheDocument();
      expect(countText()).toBe('0 users');
    });

    it('handles a user with no name', async () => {
      adminApi.listUsers.mockResolvedValue([{ ...ADA, first_name: '', last_name: '' }]);

      renderWithQuery(<AdminUsersPage />);

      expect(await screen.findByText('user1@wildframe.test')).toBeInTheDocument();
    });
  });

  describe('pagination', () => {
    beforeEach(() => {
      adminApi.listUsers.mockResolvedValue(makeUsers(25));
    });

    it('shows ten rows per page with a page counter', async () => {
      renderWithQuery(<AdminUsersPage />);

      // Sorted newest-first, so First25 leads page one.
      expect(await screen.findByText('First25 Last25')).toBeInTheDocument();
      expect(dataRows()).toHaveLength(10);
      expect(screen.getByText('1 / 3')).toBeInTheDocument();
      expect(countText()).toBe('25 users');
    });

    it('advances and rewinds through the pages', async () => {
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First25 Last25');

      fireEvent.click(pagerButton('Next'));
      expect(await screen.findByText('First15 Last15')).toBeInTheDocument();
      expect(screen.queryByText('First25 Last25')).toBeNull();
      expect(screen.getByText('2 / 3')).toBeInTheDocument();

      fireEvent.click(pagerButton('Previous'));
      expect(await screen.findByText('First25 Last25')).toBeInTheDocument();
      expect(screen.getByText('1 / 3')).toBeInTheDocument();
    });

    it('disables the edges of the range', async () => {
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First25 Last25');

      expect(pagerButton('Previous')).toBeDisabled();
      expect(pagerButton('Next')).toBeEnabled();

      fireEvent.click(pagerButton('Next'));
      fireEvent.click(pagerButton('Next'));
      await screen.findByText('3 / 3');
      expect(await screen.findByText('First1 Last1')).toBeInTheDocument();

      expect(pagerButton('Next')).toBeDisabled();
      expect(pagerButton('Previous')).toBeEnabled();
    });

    it('shows a short final page', async () => {
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First25 Last25');

      fireEvent.click(pagerButton('Next'));
      fireEvent.click(pagerButton('Next'));
      await screen.findByText('3 / 3');

      // 25 users over 3 pages leaves 5 on the last one.
      expect(dataRows()).toHaveLength(5);
    });
  });

  describe('row menu', () => {
    it('offers details and every moderation action', async () => {
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');

      openRowMenu();

      await waitFor(() =>
        expect(document.querySelectorAll('[role="menuitem"]')).toHaveLength(4),
      );
      const labels = Array.from(document.querySelectorAll('[role="menuitem"]')).map((el) =>
        (el.textContent ?? '').trim(),
      );
      expect(labels).toEqual(['View details', 'Activate', 'Suspend', 'Ban']);
    });

    it('opens a detail drawer for the chosen row', async () => {
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');

      openRowMenu();
      await chooseMenuItem(/View details/);

      expect(await screen.findByRole('dialog', { name: 'User details' })).toBeInTheDocument();
      // The drawer must show the full id, not the truncated table cell.
      expect(screen.getByText('user-1-abcdefghijklmnop')).toBeInTheDocument();
    });

    it('describes the moderation reason carried by the user', async () => {
      adminApi.listUsers.mockResolvedValue([
        { ...ADA, status: 'suspended', reason: 'chargeback fraud' },
      ]);

      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');

      openRowMenu();
      await chooseMenuItem(/View details/);

      expect(await screen.findByText('chargeback fraud')).toBeInTheDocument();
    });

    it('shows a placeholder when a user has no moderation reason', async () => {
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');

      openRowMenu();
      await chooseMenuItem(/View details/);

      const dialog = await screen.findByRole('dialog', { name: 'User details' });
      expect(within(dialog).getByText('—')).toBeInTheDocument();
    });
  });

  describe('moderation', () => {
    it('asks for confirmation before banning', async () => {
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');

      openRowMenu();
      await chooseMenuItem(/Ban/);

      const dialog = await screen.findByRole('dialog', { name: 'Ban user' });
      expect(
        within(dialog).getByText(/You are about to set First1 Last1 \(user1@wildframe\.test\) to "banned"/),
      ).toBeInTheDocument();
      expect(adminApi.moderateUser).not.toHaveBeenCalled();
    });

    it('applies a ban once confirmed', async () => {
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');

      openRowMenu();
      await chooseMenuItem(/Ban/);
      const dialog = await screen.findByRole('dialog', { name: 'Ban user' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Ban' }));

      await waitFor(() =>
        expect(adminApi.moderateUser).toHaveBeenCalledWith(
          'user-1-abcdefghijklmnop',
          'banned',
          undefined,
        ),
      );
    });

    it('carries the existing moderation reason into a repeated action', async () => {
      adminApi.listUsers.mockResolvedValue([
        { ...ADA, status: 'suspended', reason: 'chargeback fraud' },
      ]);

      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');

      openRowMenu();
      await chooseMenuItem(/Ban/);
      const dialog = await screen.findByRole('dialog', { name: 'Ban user' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Ban' }));

      // Re-banning without the original reason would erase the audit trail.
      await waitFor(() =>
        expect(adminApi.moderateUser).toHaveBeenCalledWith(
          'user-1-abcdefghijklmnop',
          'banned',
          'chargeback fraud',
        ),
      );
    });

    it('suspends a user', async () => {
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');

      openRowMenu();
      await chooseMenuItem(/Suspend/);
      const dialog = await screen.findByRole('dialog', { name: 'Suspend user' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Suspend' }));

      await waitFor(() =>
        expect(adminApi.moderateUser).toHaveBeenCalledWith(
          'user-1-abcdefghijklmnop',
          'suspended',
          undefined,
        ),
      );
    });

    it('reactivates a user', async () => {
      adminApi.listUsers.mockResolvedValue([{ ...ADA, status: 'banned' }]);
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');

      openRowMenu();
      await chooseMenuItem(/Activate/);
      const dialog = await screen.findByRole('dialog', { name: 'Activate user' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Activate' }));

      await waitFor(() =>
        expect(adminApi.moderateUser).toHaveBeenCalledWith(
          'user-1-abcdefghijklmnop',
          'active',
          undefined,
        ),
      );
    });

    it('leaves the list untouched when the confirmation is cancelled', async () => {
      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');

      openRowMenu();
      await chooseMenuItem(/Ban/);
      const dialog = await screen.findByRole('dialog', { name: 'Ban user' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));

      // Dismissing the dialog must not have banned anyone.
      expect(adminApi.moderateUser).not.toHaveBeenCalled();
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    });

    it('keeps the row visible when moderation fails', async () => {
      adminApi.moderateUser.mockRejectedValue({
        response: { data: { detail: 'user is the last administrator' } },
      });

      renderWithQuery(<AdminUsersPage />);
      await screen.findByText('First1 Last1');

      openRowMenu();
      await chooseMenuItem(/Ban/);
      const dialog = await screen.findByRole('dialog', { name: 'Ban user' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Ban' }));

      await waitFor(() => expect(adminApi.moderateUser).toHaveBeenCalled());
      // The dialog only closes on success, so the operator still sees the
      // failure state and can retry or cancel.
      expect(screen.getByText('First1 Last1')).toBeInTheDocument();
    });
  });
});
