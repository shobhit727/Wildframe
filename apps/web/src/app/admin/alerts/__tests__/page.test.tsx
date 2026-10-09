import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import AdminAlertsPage from '@/app/admin/alerts/page';
import { renderWithQuery } from '@/__tests__/app-helpers';
import type { SystemAlert } from '@/types/admin';

const { adminApi } = vi.hoisted(() => ({
  adminApi: {
    listAlerts: vi.fn(),
    createAlert: vi.fn(),
    acknowledgeAlert: vi.fn(),
    listUsers: vi.fn(),
    listFlags: vi.fn(),
    listConfigs: vi.fn(),
    listAuditLogs: vi.fn(),
    moderateUser: vi.fn(),
    resolveFlag: vi.fn(),
    setConfig: vi.fn(),
  },
}));

vi.mock('@/api/admin', () => adminApi);

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/admin/alerts',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const ALERT: SystemAlert = {
  id: 1,
  alert_type: 'high-latency',
  severity: 'critical',
  message: 'p95 above 2s on the gateway',
  service: 'api-gateway',
  acknowledged: false,
  created_at: '2024-05-01T10:00:00Z',
};

const PLACEHOLDERS = {
  type: 'e.g. high-latency',
  service: 'e.g. streaming-service',
  message: 'Describe the issue…',
} as const;

function listItems(): HTMLElement[] {
  return within(screen.getByRole('list'))
    .queryAllByRole('listitem');
}

async function openCreateDrawer(): Promise<HTMLElement> {
  fireEvent.click(screen.getByRole('button', { name: /New alert/ }));
  return screen.findByRole('dialog', { name: 'Create system alert' });
}

/**
 * The drawer inputs are addressed by placeholder, not by label: the shared
 * `Field` component renders a <label> with no `htmlFor` and the inputs have no
 * id, so there is no accessible name to query. Reported as an a11y bug.
 */
function fillDrawer(dialog: HTMLElement, values: Partial<Record<keyof typeof PLACEHOLDERS, string>>) {
  for (const [field, value] of Object.entries(values)) {
    fireEvent.change(
      within(dialog).getByPlaceholderText(PLACEHOLDERS[field as keyof typeof PLACEHOLDERS]),
      { target: { value } },
    );
  }
}

beforeEach(() => {
  for (const fn of Object.values(adminApi)) fn.mockReset();
  adminApi.listAlerts.mockResolvedValue([ALERT]);
  adminApi.createAlert.mockResolvedValue({});
  adminApi.acknowledgeAlert.mockResolvedValue({});
});

describe('AdminAlertsPage (/admin/alerts)', () => {
  it('requests the alert feed', async () => {
    renderWithQuery(<AdminAlertsPage />);

    await waitFor(() => expect(adminApi.listAlerts).toHaveBeenCalledWith({ limit: 50 }));
  });

  it('lists an alert with its type, severity, service and message', async () => {
    renderWithQuery(<AdminAlertsPage />);

    expect(await screen.findByText('high-latency')).toBeInTheDocument();
    expect(screen.getByText('critical')).toBeInTheDocument();
    expect(screen.getByText('api-gateway')).toBeInTheDocument();
    expect(screen.getByText('p95 above 2s on the gateway')).toBeInTheDocument();
    expect(listItems()).toHaveLength(1);
  });

  it('reports an all-clear feed when nothing needs attention', async () => {
    adminApi.listAlerts.mockResolvedValue([]);

    renderWithQuery(<AdminAlertsPage />);

    expect(await screen.findByText('All clear')).toBeInTheDocument();
    expect(screen.getByText('No unacknowledged alerts right now.')).toBeInTheDocument();
  });

  it('keeps the page usable when the alert lookup fails', async () => {
    adminApi.listAlerts.mockRejectedValue(new Error('admin 503'));

    renderWithQuery(<AdminAlertsPage />);

    // A failed read must not read as "all clear" to an operator, but this
    // component has no error state — it falls through to the empty branch.
    expect(await screen.findByText('All clear')).toBeInTheDocument();
  });

  describe('acknowledgement', () => {
    it('offers acknowledgement only for an unacknowledged alert', async () => {
      renderWithQuery(<AdminAlertsPage />);
      await screen.findByText('high-latency');

      expect(screen.getByRole('button', { name: /Acknowledge/ })).toBeInTheDocument();
    });

    it('hides the action and names who acknowledged an alert', async () => {
      adminApi.listAlerts.mockResolvedValue([
        { ...ALERT, acknowledged: true, acknowledged_by: 'grace' },
      ]);

      renderWithQuery(<AdminAlertsPage />);

      expect(await screen.findByText(/acknowledged by grace/)).toBeInTheDocument();
      // Re-acknowledging would produce a second audit event for the same alert.
      expect(screen.queryByRole('button', { name: /Acknowledge/ })).toBeNull();
    });

    it('marks an acknowledged alert with no actor as still acknowledged', async () => {
      adminApi.listAlerts.mockResolvedValue([{ ...ALERT, acknowledged: true }]);

      renderWithQuery(<AdminAlertsPage />);

      expect(await screen.findByText(/acknowledged/)).toBeInTheDocument();
      expect(screen.queryByText(/by /)).toBeNull();
    });

    it('confirms which alert is being acknowledged', async () => {
      renderWithQuery(<AdminAlertsPage />);
      await screen.findByText('high-latency');

      fireEvent.click(screen.getByRole('button', { name: /Acknowledge/ }));

      const dialog = await screen.findByRole('dialog', { name: 'Acknowledge alert' });
      expect(
        within(dialog).getByText('"high-latency" on api-gateway will be marked as resolved.'),
      ).toBeInTheDocument();
      expect(adminApi.acknowledgeAlert).not.toHaveBeenCalled();
    });

    it('acknowledges the alert once confirmed', async () => {
      renderWithQuery(<AdminAlertsPage />);
      await screen.findByText('high-latency');

      fireEvent.click(screen.getByRole('button', { name: /Acknowledge/ }));
      const dialog = await screen.findByRole('dialog', { name: 'Acknowledge alert' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Acknowledge' }));

      await waitFor(() => expect(adminApi.acknowledgeAlert).toHaveBeenCalledWith(1));
    });

    it('acknowledges nothing when the dialog is dismissed', async () => {
      renderWithQuery(<AdminAlertsPage />);
      await screen.findByText('high-latency');

      fireEvent.click(screen.getByRole('button', { name: /Acknowledge/ }));
      const dialog = await screen.findByRole('dialog', { name: 'Acknowledge alert' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));

      expect(adminApi.acknowledgeAlert).not.toHaveBeenCalled();
    });

    it('keeps the alert listed when acknowledgement fails', async () => {
      adminApi.acknowledgeAlert.mockRejectedValue({
        response: { data: { detail: 'alert already acknowledged' } },
      });

      renderWithQuery(<AdminAlertsPage />);
      await screen.findByText('high-latency');

      fireEvent.click(screen.getByRole('button', { name: /Acknowledge/ }));
      const dialog = await screen.findByRole('dialog', { name: 'Acknowledge alert' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Acknowledge' }));

      await waitFor(() => expect(adminApi.acknowledgeAlert).toHaveBeenCalled());
      expect(screen.getByText('high-latency')).toBeInTheDocument();
    });
  });

  describe('create drawer', () => {
    it('opens with every field blank', async () => {
      renderWithQuery(<AdminAlertsPage />);

      const drawer = await openCreateDrawer();
      expect(within(drawer).getByPlaceholderText(PLACEHOLDERS.type)).toHaveValue('');
      expect(within(drawer).getByPlaceholderText(PLACEHOLDERS.service)).toHaveValue('');
      expect(within(drawer).getByPlaceholderText(PLACEHOLDERS.message)).toHaveValue('');
    });

    it('keeps create disabled until type, service and message are supplied', async () => {
      renderWithQuery(<AdminAlertsPage />);

      const drawer = await openCreateDrawer();
      const create = within(drawer).getByRole('button', { name: 'Create alert' });
      // A half-specified broadcast would reach every operator as a broken page.
      expect(create).toBeDisabled();

      fillDrawer(drawer, { type: 'disk-pressure' });
      expect(create).toBeDisabled();

      fillDrawer(drawer, { service: 'media-pipeline' });
      expect(create).toBeDisabled();

      fillDrawer(drawer, { message: 'Root volume at 91%' });
      expect(create).toBeEnabled();
    });

    it('stays disabled when only the message is supplied', async () => {
      renderWithQuery(<AdminAlertsPage />);

      const drawer = await openCreateDrawer();
      fillDrawer(drawer, { message: 'Root volume at 91%' });

      expect(within(drawer).getByRole('button', { name: 'Create alert' })).toBeDisabled();
    });

    it('creates the alert with the selected severity', async () => {
      renderWithQuery(<AdminAlertsPage />);

      const drawer = await openCreateDrawer();
      fillDrawer(drawer, {
        type: 'disk-pressure',
        service: 'media-pipeline',
        message: 'Root volume at 91%',
      });
      fireEvent.change(within(drawer).getByRole('combobox'), { target: { value: 'critical' } });
      fireEvent.click(within(drawer).getByRole('button', { name: 'Create alert' }));

      await waitFor(() =>
        expect(adminApi.createAlert).toHaveBeenCalledWith({
          alert_type: 'disk-pressure',
          severity: 'critical',
          message: 'Root volume at 91%',
          service: 'media-pipeline',
        }),
      );
    });

    it('defaults the severity to warning', async () => {
      renderWithQuery(<AdminAlertsPage />);

      const drawer = await openCreateDrawer();
      expect(within(drawer).getByRole('combobox')).toHaveValue('warning');
    });

    it('offers every severity level', async () => {
      renderWithQuery(<AdminAlertsPage />);

      const drawer = await openCreateDrawer();
      const options = within(drawer)
        .getAllByRole('option')
        .map((o) => (o as HTMLOptionElement).value);
      expect(options).toEqual(['info', 'warning', 'critical']);
    });

    it('closes the drawer once the alert is created', async () => {
      renderWithQuery(<AdminAlertsPage />);

      const drawer = await openCreateDrawer();
      fillDrawer(drawer, {
        type: 'disk-pressure',
        service: 'media-pipeline',
        message: 'Root volume at 91%',
      });
      fireEvent.click(within(drawer).getByRole('button', { name: 'Create alert' }));

      await waitFor(() => expect(adminApi.createAlert).toHaveBeenCalled());
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    });

    it('keeps the draft after cancel (reported bug)', async () => {
      renderWithQuery(<AdminAlertsPage />);

      const drawer = await openCreateDrawer();
      fillDrawer(drawer, { type: 'disk-pressure', service: 'media-pipeline' });
      fireEvent.click(within(drawer).getByRole('button', { name: 'Cancel' }));

      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

      // BUG (reported): the Cancel button only closes the drawer — it never
      // resets `form`. The reset lives in submit()'s onSuccess, so a cancelled
      // draft silently reappears on reopen. An operator can re-send an alert
      // they thought they abandoned, and there is no on-screen hint that the
      // fields are pre-filled from a previous, never-broadcast attempt.
      const reopened = await openCreateDrawer();
      expect(within(reopened).getByPlaceholderText(PLACEHOLDERS.type)).toHaveValue(
        'disk-pressure',
      );
      expect(within(reopened).getByPlaceholderText(PLACEHOLDERS.service)).toHaveValue(
        'media-pipeline',
      );
    });

    it('keeps the draft and the drawer open when creation fails', async () => {
      adminApi.createAlert.mockRejectedValue({ response: { data: { detail: 'bad type' } } });

      renderWithQuery(<AdminAlertsPage />);

      const drawer = await openCreateDrawer();
      fillDrawer(drawer, {
        type: 'disk-pressure',
        service: 'media-pipeline',
        message: 'Root volume at 91%',
      });
      fireEvent.click(within(drawer).getByRole('button', { name: 'Create alert' }));

      await waitFor(() => expect(adminApi.createAlert).toHaveBeenCalled());
      // Losing the operator's composed message on a transient failure would be
      // the worst possible time to make them retype it.
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(within(screen.getByRole('dialog')).getByPlaceholderText(PLACEHOLDERS.type)).toHaveValue(
        'disk-pressure',
      );
    });
  });
});
