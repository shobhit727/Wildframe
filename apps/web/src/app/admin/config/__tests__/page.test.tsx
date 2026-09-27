import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import AdminConfigPage from '@/app/admin/config/page';
import { renderWithQuery } from '@/__tests__/app-helpers';
import type { SystemConfig } from '@/types/admin';

const { adminApi } = vi.hoisted(() => ({
  adminApi: {
    listConfigs: vi.fn(),
    setConfig: vi.fn(),
    listUsers: vi.fn(),
    listFlags: vi.fn(),
    listAlerts: vi.fn(),
    listAuditLogs: vi.fn(),
    moderateUser: vi.fn(),
    resolveFlag: vi.fn(),
    createAlert: vi.fn(),
    acknowledgeAlert: vi.fn(),
  },
}));

vi.mock('@/api/admin', () => adminApi);

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/admin/config',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const RATE_LIMIT: SystemConfig = {
  id: 1,
  key: 'rate_limit.rps',
  value: '250',
  config_type: 'integer',
  description: 'Gateway requests per second',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const FEATURE: SystemConfig = {
  id: 2,
  key: 'feature.new_billing',
  value: 'false',
  config_type: 'boolean',
  description: null,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const PLACEHOLDERS = {
  key: 'feature.flag_name',
  description: 'What does this control?',
  value: 'value',
  json: '{"enabled": true}',
} as const;

/**
 * The drawer inputs are addressed by placeholder: the shared `Field` renders a
 * <label> with no `htmlFor` and the inputs carry no id, so there is no
 * accessible name to query on. Reported as an a11y bug.
 */
function fillDrawer(
  dialog: HTMLElement,
  values: Partial<Record<keyof typeof PLACEHOLDERS | 'rawValue', string>>,
) {
  for (const [field, value] of Object.entries(values)) {
    if (field === 'rawValue') {
      fireEvent.change(within(dialog).getByPlaceholderText(PLACEHOLDERS.value), {
        target: { value },
      });
      continue;
    }
    fireEvent.change(within(dialog).getByPlaceholderText(PLACEHOLDERS[field as keyof typeof PLACEHOLDERS]), {
      target: { value },
    });
  }
}

function dataRows(): HTMLElement[] {
  return within(screen.getByRole('table'))
    .getAllByRole('row')
    .slice(1);
}

beforeEach(() => {
  for (const fn of Object.values(adminApi)) fn.mockReset();
  adminApi.listConfigs.mockResolvedValue([RATE_LIMIT, FEATURE]);
  adminApi.setConfig.mockResolvedValue({});
});

describe('AdminConfigPage (/admin/config)', () => {
  it('requests the full config surface', async () => {
    renderWithQuery(<AdminConfigPage />);

    await waitFor(() => expect(adminApi.listConfigs).toHaveBeenCalledWith({ limit: 200 }));
  });

  it('lists each key with its value, type and description', async () => {
    renderWithQuery(<AdminConfigPage />);

    expect(await screen.findByText('rate_limit.rps')).toBeInTheDocument();
    expect(screen.getByText('250')).toBeInTheDocument();
    expect(screen.getByText('integer')).toBeInTheDocument();
    expect(screen.getByText('Gateway requests per second')).toBeInTheDocument();
    expect(dataRows()).toHaveLength(2);
  });

  it('shows an em dash for a key with no description', async () => {
    renderWithQuery(<AdminConfigPage />);
    await screen.findByText('feature.new_billing');

    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('prompts creation when no config exists', async () => {
    adminApi.listConfigs.mockResolvedValue([]);

    renderWithQuery(<AdminConfigPage />);

    expect(await screen.findByText('No config entries')).toBeInTheDocument();
    expect(screen.getByText('Create a key to get started.')).toBeInTheDocument();
  });

  it('keeps the page usable when the config lookup fails', async () => {
    adminApi.listConfigs.mockRejectedValue(new Error('admin 503'));

    renderWithQuery(<AdminConfigPage />);

    expect(await screen.findByText('No config entries')).toBeInTheDocument();
  });

  describe('search', () => {
    it('filters by key', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.change(screen.getByPlaceholderText('Search by key, value, or description…'), {
        target: { value: 'feature' },
      });

      await waitFor(() => expect(screen.queryByText('rate_limit.rps')).toBeNull());
      expect(screen.getByText('feature.new_billing')).toBeInTheDocument();
    });

    it('filters by value', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.change(screen.getByPlaceholderText('Search by key, value, or description…'), {
        target: { value: '250' },
      });

      expect(screen.getByText('rate_limit.rps')).toBeInTheDocument();
      expect(screen.queryByText('feature.new_billing')).toBeNull();
    });

    it('filters by description', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.change(screen.getByPlaceholderText('Search by key, value, or description…'), {
        target: { value: 'gateway requests' },
      });

      expect(screen.getByText('rate_limit.rps')).toBeInTheDocument();
      expect(screen.queryByText('feature.new_billing')).toBeNull();
    });

    it('prompts creation when the search matches nothing', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.change(screen.getByPlaceholderText('Search by key, value, or description…'), {
        target: { value: 'no-such-key' },
      });

      expect(await screen.findByText('No config entries')).toBeInTheDocument();
    });
  });

  describe('editing an existing key', () => {
    it('pre-fills the drawer and names the key in the title', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.click(within(dataRows()[0]).getByRole('button', { name: 'Edit' }));

      const drawer = await screen.findByRole('dialog', { name: 'Edit "rate_limit.rps"' });
      expect(within(drawer).getByPlaceholderText(PLACEHOLDERS.key)).toHaveValue('rate_limit.rps');
      expect(within(drawer).getByPlaceholderText(PLACEHOLDERS.value)).toHaveValue('250');
      expect(within(drawer).getByPlaceholderText(PLACEHOLDERS.description)).toHaveValue(
        'Gateway requests per second',
      );
      expect(within(drawer).getByRole('combobox')).toHaveValue('integer');
    });

    it('locks the key so an existing entry cannot be renamed by accident', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.click(within(dataRows()[0]).getByRole('button', { name: 'Edit' }));
      const drawer = await screen.findByRole('dialog', { name: 'Edit "rate_limit.rps"' });

      // Silently writing a new key would orphan the old one instead of
      // updating it, with no way to tell from the response.
      expect(within(drawer).getByPlaceholderText(PLACEHOLDERS.key)).toBeDisabled();
    });

    it('saves the edited value against the original key', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.click(within(dataRows()[0]).getByRole('button', { name: 'Edit' }));
      const drawer = await screen.findByRole('dialog', { name: 'Edit "rate_limit.rps"' });
      fireEvent.change(within(drawer).getByPlaceholderText(PLACEHOLDERS.value), {
        target: { value: '500' },
      });
      fireEvent.click(within(drawer).getByRole('button', { name: 'Save' }));

      await waitFor(() =>
        expect(adminApi.setConfig).toHaveBeenCalledWith({
          key: 'rate_limit.rps',
          value: '500',
          config_type: 'integer',
          description: 'Gateway requests per second',
        }),
      );
    });

    it('keeps the drawer open when the save fails', async () => {
      adminApi.setConfig.mockRejectedValue({ response: { data: { detail: 'unknown key' } } });

      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.click(within(dataRows()[0]).getByRole('button', { name: 'Edit' }));
      const drawer = await screen.findByRole('dialog', { name: 'Edit "rate_limit.rps"' });
      fireEvent.change(within(drawer).getByPlaceholderText(PLACEHOLDERS.value), {
        target: { value: '500' },
      });
      fireEvent.click(within(drawer).getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(adminApi.setConfig).toHaveBeenCalled());
      // Closing on failure would discard the edit with no confirmation.
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });

  describe('creating a new key', () => {
    it('opens an empty drawer with the string type preselected', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.click(screen.getByRole('button', { name: /New config/ }));
      const drawer = await screen.findByRole('dialog', { name: 'New config entry' });

      expect(within(drawer).getByPlaceholderText(PLACEHOLDERS.key)).toHaveValue('');
      expect(within(drawer).getByPlaceholderText(PLACEHOLDERS.key)).toBeEnabled();
      expect(within(drawer).getByRole('combobox')).toHaveValue('string');
    });

    it('keeps save disabled until both key and value are present', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.click(screen.getByRole('button', { name: /New config/ }));
      const drawer = await screen.findByRole('dialog', { name: 'New config entry' });
      const save = within(drawer).getByRole('button', { name: 'Save' });

      // An empty key would write a config nobody can look up later.
      expect(save).toBeDisabled();

      fillDrawer(drawer, { key: 'feature.dark_mode' });
      expect(save).toBeDisabled();

      fillDrawer(drawer, { rawValue: 'on' });
      expect(save).toBeEnabled();
    });

    it('offers every supported config type', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.click(screen.getByRole('button', { name: /New config/ }));
      const drawer = await screen.findByRole('dialog', { name: 'New config entry' });

      const options = within(drawer)
        .getAllByRole('option')
        .map((o) => (o as HTMLOptionElement).value);
      expect(options).toEqual(['string', 'integer', 'boolean', 'json']);
    });

    it('swaps in JSON guidance and a JSON placeholder for json values', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.click(screen.getByRole('button', { name: /New config/ }));
      const drawer = await screen.findByRole('dialog', { name: 'New config entry' });

      expect(within(drawer).queryByText('Valid JSON string')).toBeNull();
      expect(within(drawer).getByPlaceholderText(PLACEHOLDERS.value)).toBeInTheDocument();

      fireEvent.change(within(drawer).getByRole('combobox'), { target: { value: 'json' } });

      // Storing malformed JSON here will fail at the service that reads it,
      // far from the operator who typed it.
      expect(within(drawer).getByText('Valid JSON string')).toBeInTheDocument();
      expect(within(drawer).getByPlaceholderText(PLACEHOLDERS.json)).toBeInTheDocument();
    });

    it('creates the key with its chosen type', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.click(screen.getByRole('button', { name: /New config/ }));
      const drawer = await screen.findByRole('dialog', { name: 'New config entry' });
      fillDrawer(drawer, { key: 'feature.dark_mode', rawValue: 'on' });
      fireEvent.change(within(drawer).getByRole('combobox'), { target: { value: 'boolean' } });
      fillDrawer(drawer, { description: 'Dark theme rollout' });
      fireEvent.click(within(drawer).getByRole('button', { name: 'Save' }));

      await waitFor(() =>
        expect(adminApi.setConfig).toHaveBeenCalledWith({
          key: 'feature.dark_mode',
          value: 'on',
          config_type: 'boolean',
          description: 'Dark theme rollout',
        }),
      );
    });

    it('closes the drawer once the key is saved', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.click(screen.getByRole('button', { name: /New config/ }));
      const drawer = await screen.findByRole('dialog', { name: 'New config entry' });
      fillDrawer(drawer, { key: 'feature.dark_mode', rawValue: 'on' });
      fireEvent.click(within(drawer).getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(adminApi.setConfig).toHaveBeenCalled());
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    });

    it('discards the edit without saving when cancelled', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.click(screen.getByRole('button', { name: /New config/ }));
      const drawer = await screen.findByRole('dialog', { name: 'New config entry' });
      fillDrawer(drawer, { key: 'feature.dark_mode', rawValue: 'on' });
      fireEvent.click(within(drawer).getByRole('button', { name: 'Cancel' }));

      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      expect(adminApi.setConfig).not.toHaveBeenCalled();
    });

    it('does not mutate an existing key when a new entry is saved', async () => {
      renderWithQuery(<AdminConfigPage />);
      await screen.findByText('rate_limit.rps');

      fireEvent.click(screen.getByRole('button', { name: /New config/ }));
      const drawer = await screen.findByRole('dialog', { name: 'New config entry' });
      fillDrawer(drawer, { key: 'feature.dark_mode', rawValue: 'on' });
      fireEvent.click(within(drawer).getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(adminApi.setConfig).toHaveBeenCalled());
      // The payload must name the new key, never the row that was edited first.
      expect(adminApi.setConfig).toHaveBeenCalledWith(
        expect.objectContaining({ key: 'feature.dark_mode' }),
      );
    });
  });
});
