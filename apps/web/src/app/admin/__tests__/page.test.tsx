import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import AdminDashboardPage from '@/app/admin/page';
import { renderWithQuery } from '@/__tests__/app-helpers';
import type { AdminUser, SystemStats } from '@/types/admin';

const { adminApi } = vi.hoisted(() => ({
  adminApi: {
    getSystemStats: vi.fn(),
    listUsers: vi.fn(),
    listFlags: vi.fn(),
    listAlerts: vi.fn(),
    listConfigs: vi.fn(),
    listAuditLogs: vi.fn(),
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
  usePathname: () => '/admin',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const STATS: SystemStats = {
  total_users: 12345,
  active_users: 1000,
  suspended_users: 12,
  flagged_content: 7,
  active_alerts: 3,
  system_uptime_hours: 30,
};

function adminUser(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    id: 'row-1',
    user_id: 'u-1',
    email: 'ada@wildframe.test',
    first_name: 'Ada',
    last_name: 'Lovelace',
    status: 'active',
    created_at: '2024-05-01T00:00:00Z',
    updated_at: '2024-05-01T00:00:00Z',
    ...overrides,
  };
}

/**
 * The whole StatCard tile for a label. StatCard nests the label/value pair in
 * an inner flex row and puts the trend/hint row outside it, so two levels above
 * the label <p> is the card itself.
 */
function statCard(label: string): HTMLElement {
  return screen.getByText(label).closest('div')?.parentElement?.parentElement as HTMLElement;
}

beforeEach(() => {
  for (const fn of Object.values(adminApi)) fn.mockReset();
  adminApi.getSystemStats.mockResolvedValue(STATS);
  adminApi.listUsers.mockResolvedValue([adminUser()]);
});

describe('AdminDashboardPage (/admin)', () => {
  it('shows skeleton placeholders until the stats request settles', () => {
    adminApi.getSystemStats.mockReturnValue(new Promise(() => {}));

    const { container } = renderWithQuery(<AdminDashboardPage />);

    // Reporting "0 users" before the response arrives would be a false claim
    // about the platform; placeholders must stand in.
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
    expect(screen.queryByText('Total Users')).toBeNull();
  });

  it('formats the headline user counts for an operator', async () => {
    renderWithQuery(<AdminDashboardPage />);

    expect(await screen.findByText('Total Users')).toBeInTheDocument();
    expect(statCard('Total Users')).toHaveTextContent('12,345');
    expect(statCard('Total Users')).toHaveTextContent('1,000 active');
  });

  it('derives active streams from the active user count', async () => {
    renderWithQuery(<AdminDashboardPage />);

    await screen.findByText('Total Users');
    // 1000 * 0.32 = 320.
    expect(statCard('Active Streams')).toHaveTextContent('320');
  });

  it('rounds the derived active-stream figure', async () => {
    adminApi.getSystemStats.mockResolvedValue({ ...STATS, active_users: 101 });

    renderWithQuery(<AdminDashboardPage />);

    await screen.findByText('Total Users');
    // 101 * 0.32 = 32.32, displayed as 32.
    expect(statCard('Active Streams')).toHaveTextContent('32');
  });

  it('pairs open flags with the unacknowledged alert count', async () => {
    renderWithQuery(<AdminDashboardPage />);

    await screen.findByText('Total Users');
    expect(statCard('Open Flags')).toHaveTextContent('7');
    expect(statCard('Open Flags')).toHaveTextContent('3 alerts');
  });

  it('formats uptime in days and hours past the first day', async () => {
    renderWithQuery(<AdminDashboardPage />);

    await screen.findByText('Total Users');
    // 30 hours = 1d 6h.
    expect(statCard('MRR')).toHaveTextContent('uptime 1d 6h');
  });

  it('formats uptime in hours below a full day', async () => {
    adminApi.getSystemStats.mockResolvedValue({ ...STATS, system_uptime_hours: 5.4 });

    renderWithQuery(<AdminDashboardPage />);

    await screen.findByText('Total Users');
    expect(statCard('MRR')).toHaveTextContent('uptime 5h');
  });

  it('describes the traffic chart for screen readers', async () => {
    renderWithQuery(<AdminDashboardPage />);

    expect(
      await screen.findByRole('img', { name: /relative traffic activity over fourteen days/i }),
    ).toBeInTheDocument();
  });

  describe('new users', () => {
    it('lists the most recent registrations', async () => {
      renderWithQuery(<AdminDashboardPage />);

      expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument();
      expect(screen.getByText('ada@wildframe.test')).toBeInTheDocument();
      expect(adminApi.listUsers).toHaveBeenCalledWith({ limit: 6 });
    });

    it('handles a registrant with no name', async () => {
      adminApi.listUsers.mockResolvedValue([
        adminUser({ first_name: '', last_name: '', email: 'anon@wildframe.test' }),
      ]);

      renderWithQuery(<AdminDashboardPage />);

      expect(await screen.findByText('anon@wildframe.test')).toBeInTheDocument();
    });

    it('shows an empty state when nobody has registered', async () => {
      adminApi.listUsers.mockResolvedValue([]);

      renderWithQuery(<AdminDashboardPage />);

      expect(await screen.findByText('No users yet')).toBeInTheDocument();
      expect(
        screen.getByText('User accounts will appear here as they register.'),
      ).toBeInTheDocument();
    });

    it('links onward to the full user list', async () => {
      renderWithQuery(<AdminDashboardPage />);

      const viewAll = await screen.findByRole('link', { name: 'View all' });
      expect(viewAll).toHaveAttribute('href', '/admin/users');
    });
  });

  describe('refresh', () => {
    it('re-requests the stats on demand', async () => {
      renderWithQuery(<AdminDashboardPage />);
      await screen.findByText('Total Users');
      expect(adminApi.getSystemStats).toHaveBeenCalledTimes(1);

      fireEvent.click(screen.getByRole('button', { name: 'Refresh data' }));

      await waitFor(() => expect(adminApi.getSystemStats).toHaveBeenCalledTimes(2));
    });

    it('reports that a refresh is in flight', async () => {
      renderWithQuery(<AdminDashboardPage />);
      await screen.findByText('Total Users');

      let release: (() => void) | undefined;
      adminApi.getSystemStats.mockImplementationOnce(
        () => new Promise((resolve) => {
          release = () => resolve(STATS);
        })
      );
      fireEvent.click(screen.getByRole('button', { name: 'Refresh data' }));

      const busy = await screen.findByRole('button', { name: 'Refreshing' });
      expect(busy).toBeDisabled();
      release?.();
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Refresh data' })).toBeEnabled(),
      );
    });

    it('leaves the previous figures on screen when a refresh fails', async () => {
      renderWithQuery(<AdminDashboardPage />);
      await screen.findByText('Total Users');

      adminApi.getSystemStats.mockRejectedValue(new Error('admin 503'));
      fireEvent.click(screen.getByRole('button', { name: 'Refresh data' }));

      // Blanking the KPIs on a failed refresh would hide known-good data during
      // exactly the incident an operator is trying to diagnose.
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Refresh data' })).toBeEnabled(),
      );
      expect(within(statCard('Total Users')).getByText('12,345')).toBeInTheDocument();
    });
  });

  it('keeps the stat grid empty when the very first stats request fails', async () => {
    adminApi.getSystemStats.mockRejectedValue(new Error('admin 503'));

    renderWithQuery(<AdminDashboardPage />);

    // `!s` holds the skeletons on forever, so the section shows placeholders
    // rather than a misleading set of zeros.
    expect(await screen.findByRole('button', { name: 'Refresh data' })).toBeInTheDocument();
    expect(screen.queryByText('Total Users')).toBeNull();
  });
});
