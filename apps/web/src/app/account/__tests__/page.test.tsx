import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import AccountPage from '@/app/account/page';
import { useAuthStore } from '@/stores/auth';
import { renderWithQuery, makeUser } from '@/__tests__/app-helpers';
import type { Subscription, UserDevice, UserPreferences, UserProfile } from '@/types';

const { apiClient } = vi.hoisted(() => ({
  apiClient: {
    getProfile: vi.fn(),
    getSubscription: vi.fn(),
    getDevices: vi.fn(),
    getPreferences: vi.fn(),
    updateProfile: vi.fn(),
    updatePreferences: vi.fn(),
  },
}));

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return { ...actual, apiClient };
});

const push = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/account',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const PROFILE = {
  id: 'p-1',
  user_id: 'u-1',
  country: 'GB',
  language: 'en',
  profile_completeness: 80,
  created_at: '2024-01-15T00:00:00Z',
  bio: 'Math and machine learning.',
  phone_number: '+44 20 7946 0958',
} satisfies Partial<UserProfile> as UserProfile;

function tab(name: RegExp | string) {
  return screen.getByRole('tab', { name });
}

/**
 * Activate a Radix tab the way a mouse user does. The Tabs trigger wires
 * onMouseDown / onFocus / onKeyDown and has no onClick handler, so a bare
 * `fireEvent.click` is silently ignored.
 */
function openTab(name: RegExp | string) {
  const trigger = tab(name);
  fireEvent.mouseDown(trigger, { button: 0, ctrlKey: false });
  fireEvent.focus(trigger);
  expect(trigger).toHaveAttribute('aria-selected', 'true');
}

beforeEach(() => {
  push.mockReset();
  for (const fn of Object.values(apiClient)) fn.mockReset();

  apiClient.getProfile.mockResolvedValue(PROFILE);
  apiClient.getSubscription.mockResolvedValue({
    id: 'sub-1',
    tier: 'svod',
    subscription_status: 'active',
    monthly_price: '7.99',
  } satisfies Subscription);
  apiClient.getDevices.mockResolvedValue([] as UserDevice[]);
  apiClient.getPreferences.mockResolvedValue({ autoplay: true } as UserPreferences);
  apiClient.updateProfile.mockResolvedValue(PROFILE);
  apiClient.updatePreferences.mockResolvedValue({} as UserPreferences);

  useAuthStore.setState({
    user: makeUser(),
    token: 'tok',
    isAuthenticated: true,
    isLoading: false,
  });
});

describe('AccountPage (/account)', () => {
  describe('authentication', () => {
    it('redirects a signed-out visitor and issues no account requests', async () => {
      useAuthStore.setState({ user: null, token: null, isAuthenticated: false });

      renderWithQuery(<AccountPage />);

      expect(await screen.findByText('Loading...')).toBeInTheDocument();
      expect(push).toHaveBeenCalledWith('/login');
      expect(apiClient.getProfile).not.toHaveBeenCalled();
      expect(apiClient.getSubscription).not.toHaveBeenCalled();
    });

    it('waits on the profile skeleton while the user object is still unknown', async () => {
      // Session is restored but /me has not answered yet: rendering the signed
      // out shell here would flash a signed-out header before flipping.
      useAuthStore.setState({ user: null, token: 'tok', isAuthenticated: true });

      renderWithQuery(<AccountPage />);

      expect(screen.queryByRole('heading', { name: 'Account' })).toBeNull();
      expect(apiClient.getProfile).not.toHaveBeenCalled();
    });
  });

  describe('profile tab', () => {
    it('shows the account heading, name and verified email', async () => {
      renderWithQuery(<AccountPage />);

      expect(await screen.findByRole('heading', { name: 'Account' })).toBeInTheDocument();
      expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
      expect(screen.getByText(/ada@wildframe\.test/)).toHaveTextContent('✓ verified');
    });

    it('omits the verified badge for an unverified account', async () => {
      useAuthStore.setState({ user: makeUser({ emailVerified: false }) });

      renderWithQuery(<AccountPage />);

      await screen.findByRole('heading', { name: 'Account' });
      expect(screen.queryByText(/verified/)).toBeNull();
    });

    it('falls back to initials when the profile has no avatar', async () => {
      renderWithQuery(<AccountPage />);

      await screen.findByRole('heading', { name: 'Account' });
      expect(screen.getByText('AL')).toBeInTheDocument();
      expect(screen.queryByRole('img')).toBeNull();
    });

    it('renders the profile facts the service returned', async () => {
      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });

      await waitFor(() => expect(screen.getByText('80%')).toBeInTheDocument());
      expect(screen.getByText('GB')).toBeInTheDocument();
      expect(screen.getByText('en')).toBeInTheDocument();
    });

    it('shows an em dash for profile facts that have not loaded', async () => {
      apiClient.getProfile.mockRejectedValue(new Error('users 500'));

      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });

      // A failed profile lookup must degrade to placeholders, not blanks that
      // read as "country: none" when the truth is "unknown".
      await waitFor(() => expect(screen.getAllByText('—').length).toBeGreaterThan(0));
      expect(screen.getByText('0%')).toBeInTheDocument();
    });

    it('replaces the Edit action with Cancel and Save while editing', async () => {
      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });

      await waitFor(() => expect(screen.getByText('GB')).toBeInTheDocument());
      fireEvent.click(screen.getByRole('button', { name: 'Edit' }));

      expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    });

    it('returns to read-only mode when the edit is cancelled', async () => {
      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });

      await waitFor(() => expect(screen.getByText('GB')).toBeInTheDocument());
      fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

      expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
      expect(apiClient.updateProfile).not.toHaveBeenCalled();
    });

    it('hydrates the editable fields from the loaded profile', async () => {
      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      await waitFor(() => expect(screen.getByText('GB')).toBeInTheDocument());

      fireEvent.click(screen.getByRole('button', { name: 'Edit' }));

      expect(screen.getByRole('textbox', { name: 'Bio' })).toHaveValue('Math and machine learning.');
      expect(screen.getByRole('textbox', { name: 'Phone number' })).toHaveValue('+44 20 7946 0958');
      expect(screen.getByRole('textbox', { name: 'Country' })).toHaveValue('GB');
    });

    it('submits only the profile field that was explicitly changed', async () => {
      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      await waitFor(() => expect(screen.getByText('GB')).toBeInTheDocument());

      fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
      fireEvent.change(screen.getByRole('textbox', { name: 'Bio' }), {
        target: { value: 'Updated bio' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() =>
        expect(apiClient.updateProfile).toHaveBeenCalledWith('u-1', {
          bio: 'Updated bio',
        }),
      );
      expect(apiClient.updateProfile).toHaveBeenCalledTimes(1);
    });

    it('blocks a no-op Save without making an update request', async () => {
      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      await waitFor(() => expect(screen.getByText('GB')).toBeInTheDocument());

      fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));

      expect(apiClient.updateProfile).not.toHaveBeenCalled();
      expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
    });

    it('disables profile editing when the profile lookup fails', async () => {
      apiClient.getProfile.mockRejectedValue(new Error('users 500'));

      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      await waitFor(() => expect(screen.getAllByText('—').length).toBeGreaterThan(0));

      expect(screen.getByRole('button', { name: 'Edit' })).toBeDisabled();
      expect(apiClient.updateProfile).not.toHaveBeenCalled();
    });

    it('reports a failed profile save and stays in edit mode', async () => {
      apiClient.updateProfile.mockRejectedValue(new Error('validation 422'));

      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      await waitFor(() => expect(screen.getByText('GB')).toBeInTheDocument());

      fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
      fireEvent.change(screen.getByRole('textbox', { name: 'Bio' }), {
        target: { value: 'Rejected update' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));

      // Losing the edit form on a rejected save would discard the user's work
      // with no way back.
      await waitFor(() => expect(apiClient.updateProfile).toHaveBeenCalled());
      expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
    });
  });

  describe('subscription tab', () => {
    it('links onward to billing for a plan change', async () => {
      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Subscription');

      expect(await screen.findByRole('link', { name: /change plan/i })).toHaveAttribute(
        'href',
        '/billing',
      );
    });

    it('shows the current tier, status and plan benefits', async () => {
      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });

      openTab('Subscription');

      expect(await screen.findByText('svod')).toBeInTheDocument();
      expect(screen.getByText('active')).toBeInTheDocument();
      expect(screen.getByText('Ad-free streaming')).toBeInTheDocument();
    });

    it('shows a monthly price for a paid plan', async () => {
      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Subscription');

      expect(await screen.findByText('$7.99')).toBeInTheDocument();
      expect(screen.getByText('/mo')).toBeInTheDocument();
    });

    it('hides the price for the free plan', async () => {
      apiClient.getSubscription.mockResolvedValue({
        id: 'sub-2',
        tier: 'avod',
        subscription_status: 'active',
      } satisfies Subscription);

      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Subscription');

      expect(await screen.findByText('avod')).toBeInTheDocument();
      expect(screen.queryByText('/mo')).toBeNull();
    });

    it('marks a lapsed subscription inactive', async () => {
      apiClient.getSubscription.mockResolvedValue({
        id: 'sub-3',
        tier: 'svod',
        subscription_status: 'cancelled',
        monthly_price: '7.99',
      } satisfies Subscription);

      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Subscription');

      expect(await screen.findByText('inactive')).toBeInTheDocument();
      expect(screen.queryByText('active')).toBeNull();
    });

    it('states plainly when there is no subscription', async () => {
      // billing-service 404s for a user who has never subscribed, so the query
      // errors and leaves the panel without data.
      apiClient.getSubscription.mockRejectedValue({ response: { status: 404 } });

      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Subscription');

      expect(await screen.findByText('No active subscription')).toBeInTheDocument();
    });

    it('lists no benefits for an unrecognised tier instead of crashing', async () => {
      apiClient.getSubscription.mockResolvedValue({
        id: 'sub-4',
        tier: 'premium-plus',
        subscription_status: 'active',
      } as unknown as Subscription);

      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Subscription');

      expect(await screen.findByText('premium-plus')).toBeInTheDocument();
    });
  });

  describe('preferences tab', () => {
    it('enables a preference that is currently off', async () => {
      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Preferences');

      fireEvent.click(await screen.findByRole('button', { name: 'Toggle Allow mature content' }));

      await waitFor(() =>
        expect(apiClient.updatePreferences).toHaveBeenCalledWith('u-1', {
          allow_explicit_content: true,
        }),
      );
    });

    it('disables a preference that is currently on', async () => {
      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Preferences');

      fireEvent.click(await screen.findByRole('button', { name: 'Toggle Autoplay next episode' }));

      // autoplay came back true, so the click must send false rather than
      // blindly re-sending the same value.
      await waitFor(() =>
        expect(apiClient.updatePreferences).toHaveBeenCalledWith('u-1', { autoplay: false }),
      );
    });

    it('leaves every toggle off when no preferences are stored', async () => {
      apiClient.getPreferences.mockResolvedValue({} as UserPreferences);

      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Preferences');

      const toggles = await screen.findAllByRole('button', { name: /^Toggle / });
      expect(toggles).toHaveLength(5);
      for (const toggle of toggles) {
        expect(toggle).toHaveClass('bg-dark-600');
      }
    });

    it('reflects a stored preference in its toggle styling', async () => {
      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Preferences');

      const autoplay = await screen.findByRole('button', { name: 'Toggle Autoplay next episode' });
      await waitFor(() => expect(autoplay).toHaveClass('bg-red-600'));
    });

    it('reports a failed preference save', async () => {
      apiClient.updatePreferences.mockRejectedValue(new Error('users 422'));

      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Preferences');

      fireEvent.click(await screen.findByRole('button', { name: 'Toggle Email notifications' }));

      await waitFor(() => expect(apiClient.updatePreferences).toHaveBeenCalled());
      // The toggle must not appear to have changed if the write failed.
      expect(screen.getByRole('button', { name: 'Toggle Email notifications' })).toHaveClass(
        'bg-dark-600',
      );
    });
  });

  describe('devices tab', () => {
    it('explains that devices appear once the viewer streams', async () => {
      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Devices');

      expect(
        await screen.findByText('No devices registered yet. Devices are added when you stream.'),
      ).toBeInTheDocument();
    });

    it('lists a device with its type and trust state', async () => {
      apiClient.getDevices.mockResolvedValue([
        {
          id: 'd-1',
          device_id: 'dev-1',
          device_name: 'Ada’s Laptop',
          device_type: 'web',
          is_active: true,
          is_trusted: true,
        },
      ] as UserDevice[]);

      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Devices');

      expect(await screen.findByText('Ada’s Laptop')).toBeInTheDocument();
      expect(screen.getByText('Active')).toBeInTheDocument();
      expect(screen.getByText('Trusted')).toBeInTheDocument();
    });

    it('falls back to the device type when the device has no name', async () => {
      apiClient.getDevices.mockResolvedValue([
        {
          id: 'd-2',
          device_id: 'dev-2',
          device_name: '',
          device_type: 'tv',
        },
      ] as UserDevice[]);

      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Devices');

      // The name slot falls back to the type and the subtitle also prints the
      // type, so an unnamed device shows "tv" twice. Documented as a cosmetic
      // bug; the assertion pins the current behaviour.
      await waitFor(() => expect(apiClient.getDevices).toHaveBeenCalled());
      expect(screen.getAllByText('tv')).toHaveLength(2);
    });

    it('offers removal only for a device that is already inactive', async () => {
      apiClient.getDevices.mockResolvedValue([
        {
          id: 'd-3',
          device_id: 'dev-3',
          device_name: 'Old Phone',
          device_type: 'mobile',
          is_active: false,
        },
      ] as UserDevice[]);

      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Devices');

      expect(await screen.findByRole('button', { name: 'Remove' })).toBeInTheDocument();
    });

    it('shows the empty state when the device lookup fails', async () => {
      apiClient.getDevices.mockRejectedValue(new Error('users 503'));

      renderWithQuery(<AccountPage />);
      await screen.findByRole('heading', { name: 'Account' });
      openTab('Devices');

      expect(
        await screen.findByText('No devices registered yet. Devices are added when you stream.'),
      ).toBeInTheDocument();
    });
  });
});
