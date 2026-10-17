import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import BillingPage from '@/app/billing/page';
import { useAuthStore } from '@/stores/auth';
import { renderWithQuery, makeUser } from '@/__tests__/app-helpers';
import type { Subscription } from '@/types';

const { apiClient } = vi.hoisted(() => ({
  apiClient: {
    getSubscription: vi.fn(),
    subscribe: vi.fn(),
    cancelSubscription: vi.fn(),
  },
}));

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return { ...actual, apiClient };
});

const push = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/billing',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

function subscription(overrides: Partial<Subscription> = {}): Subscription {
  return {
    id: 'sub-1',
    tier: 'svod',
    subscription_status: 'active',
    monthly_price: '7.99',
    ...overrides,
  };
}

/**
 * Each plan is a single <div> whose first child is the plan name, so the
 * heading's parent is the whole card (tagline, price, features, CTA).
 *
 * Located structurally: getByRole has to build the accessibility tree for the
 * whole page (three cards, each with a per-feature inline SVG) before it can
 * match on an accessible name.
 */
function planCard(name: string): HTMLElement {
  const heading = Array.from(document.querySelectorAll('h2')).find(
    (h) => (h.textContent ?? '').trim() === name,
  );
  if (!heading) throw new Error(`plan "${name}" not found`);
  return heading.parentElement as HTMLElement;
}

/** The plan card's CTA, found structurally. */
function planButton(name: string): HTMLButtonElement {
  const button = planCard(name).querySelector('button');
  if (!button) throw new Error(`plan "${name}" has no button`);
  return button;
}

/**
 * The "you are currently on the X plan" line renders the tier inside a nested
 * <span>, so its text is split across nodes and only `textContent` sees the
 * whole sentence. Scoped to <p> to avoid matching wrapper elements.
 */
function findCurrentPlanLine(): Promise<HTMLElement> {
  return screen.findByText(
    (_content, node) =>
      node?.tagName === 'P' && (node.textContent ?? '').includes('You are currently on the'),
  );
}

/** Resolves once the subscription has loaded and the current plan is marked. */
async function waitForSubscription(): Promise<void> {
  await waitFor(() =>
    expect(
      Array.from(document.querySelectorAll('button')).some(
        (b) => b.textContent === 'Current plan',
      ),
    ).toBe(true),
  );
}

beforeEach(() => {
  push.mockReset();
  for (const fn of Object.values(apiClient)) fn.mockReset();

  apiClient.getSubscription.mockResolvedValue(subscription());
  apiClient.subscribe.mockResolvedValue({});
  apiClient.cancelSubscription.mockResolvedValue({});

  useAuthStore.setState({
    user: makeUser(),
    token: 'tok',
    isAuthenticated: true,
    isLoading: false,
  });
});

describe('BillingPage (/billing)', () => {
  it('redirects a signed-out visitor away from the plans', async () => {
    useAuthStore.setState({ user: null, token: null, isAuthenticated: false });

    renderWithQuery(<BillingPage />);

    await waitFor(() => expect(push).toHaveBeenCalledWith('/login'));
    expect(apiClient.getSubscription).not.toHaveBeenCalled();
  });

  it('offers all three plans with their prices and benefits', async () => {
    renderWithQuery(<BillingPage />);

    expect(await screen.findByRole('heading', { name: 'Choose your plan' })).toBeInTheDocument();
    expect(planCard('Free')).toHaveTextContent('$0');
    expect(planCard('Premium')).toHaveTextContent('$7.99');
    expect(planCard('Pay-Per-View')).toHaveTextContent('From $3.99');
    expect(planCard('Premium')).toHaveTextContent('Download offline');
  });

  it('marks Premium as the most popular plan', async () => {
    renderWithQuery(<BillingPage />);

    await screen.findByRole('heading', { name: 'Choose your plan' });
    expect(screen.getByText('Most popular')).toBeInTheDocument();
  });

  it('appends a per-month suffix to the subscription plan only', async () => {
    renderWithQuery(<BillingPage />);
    await screen.findByRole('heading', { name: 'Choose your plan' });

    // $7.99 is a recurring price; "$0" and "From $3.99" are not, so a blanket
    // /mo would misstate the free and pay-per-view plans.
    expect(within(planCard('Premium')).getByText('/mo')).toBeInTheDocument();
    expect(within(planCard('Free')).queryByText('/mo')).toBeNull();
    expect(within(planCard('Pay-Per-View')).queryByText('/mo')).toBeNull();
  });

  describe('current subscription', () => {
    it('states the active tier in a readable sentence', async () => {
      renderWithQuery(<BillingPage />);

      const line = await findCurrentPlanLine();
      expect(line).toHaveTextContent('You are currently on the svod plan.');
    });

    it('disables the button for the plan already held', async () => {
      renderWithQuery(<BillingPage />);
      await waitForSubscription();

      const current = planButton('Premium');
      expect(current).toBeDisabled();
      expect(current).toHaveTextContent('Current plan');
    });

    it('leaves the other plans actionable', async () => {
      renderWithQuery(<BillingPage />);
      await waitForSubscription();

      expect(planButton('Free')).toBeEnabled();
      expect(planButton('Pay-Per-View')).toBeEnabled();
    });

    it('ignores a click on the disabled current-plan button', async () => {
      renderWithQuery(<BillingPage />);
      await waitForSubscription();

      fireEvent.click(planButton('Premium'));

      // Re-subscribing to the held tier would churn billing-service for nothing.
      expect(apiClient.subscribe).not.toHaveBeenCalled();
    });

    it('falls back to a neutral prompt when there is no subscription', async () => {
      apiClient.getSubscription.mockRejectedValue({ response: { status: 404 } });

      renderWithQuery(<BillingPage />);

      expect(
        await screen.findByText('Pick the plan that fits how you watch.'),
      ).toBeInTheDocument();
      expect(screen.queryByText(/You are currently on the/)).toBeNull();
      expect(screen.queryByRole('button', { name: /cancel subscription/i })).toBeNull();
    });

    it('hides the cancellation panel on the free plan', async () => {
      apiClient.getSubscription.mockResolvedValue(subscription({ tier: 'avod' }));

      renderWithQuery(<BillingPage />);
      await waitForSubscription();

      // Cancelling the free plan is meaningless; showing the panel would send
      // users to a no-op request.
      expect(screen.queryByRole('button', { name: /cancel subscription/i })).toBeNull();
      expect(planButton('Free')).toBeDisabled();
    });
  });

  describe('plan changes', () => {
    it('switches to the free plan', async () => {
      renderWithQuery(<BillingPage />);
      await waitForSubscription();

      fireEvent.click(planButton('Free'));

      await waitFor(() => expect(apiClient.subscribe).toHaveBeenCalledWith('u-1', 'avod'));
    });

    it('upgrades to the premium plan', async () => {
      apiClient.getSubscription.mockResolvedValue(subscription({ tier: 'avod' }));

      renderWithQuery(<BillingPage />);
      await waitForSubscription();

      fireEvent.click(planButton('Premium'));

      await waitFor(() => expect(apiClient.subscribe).toHaveBeenCalledWith('u-1', 'svod'));
    });

    it('enables pay-per-view', async () => {
      renderWithQuery(<BillingPage />);
      await waitForSubscription();

      fireEvent.click(planButton('Pay-Per-View'));

      await waitFor(() => expect(apiClient.subscribe).toHaveBeenCalledWith('u-1', 'tvod'));
    });

    it('reports a failed plan change without losing the current plan', async () => {
      apiClient.subscribe.mockRejectedValue(new Error('stripe 402'));

      renderWithQuery(<BillingPage />);
      await waitForSubscription();

      fireEvent.click(planButton('Pay-Per-View'));

      await waitFor(() => expect(apiClient.subscribe).toHaveBeenCalled());
      // A rejected change must leave the page on the old plan and re-enable the
      // control so the user can retry.
      expect(await findCurrentPlanLine()).toHaveTextContent('You are currently on the svod plan.');
      expect(planButton('Premium')).toHaveTextContent('Current plan');
      expect(planButton('Pay-Per-View')).toBeEnabled();
    });

    it('cancels a paid subscription', async () => {
      renderWithQuery(<BillingPage />);
      await waitForSubscription();

      fireEvent.click(screen.getByRole('button', { name: 'Cancel subscription' }));

      await waitFor(() => expect(apiClient.cancelSubscription).toHaveBeenCalledWith('u-1'));
    });

    it('explains that access survives until the period ends', async () => {
      renderWithQuery(<BillingPage />);
      await waitForSubscription();

      expect(
        screen.getByText(/access stays active until the end of the current period/i),
      ).toBeInTheDocument();
    });

    it('keeps the plans usable when the cancellation fails', async () => {
      apiClient.cancelSubscription.mockRejectedValue(new Error('billing 500'));

      renderWithQuery(<BillingPage />);
      await waitForSubscription();

      fireEvent.click(screen.getByRole('button', { name: 'Cancel subscription' }));

      await waitFor(() => expect(apiClient.cancelSubscription).toHaveBeenCalled());
      // The banner must stay put so the user still knows they are subscribed.
      expect(screen.getByRole('button', { name: 'Cancel subscription' })).toBeEnabled();
      expect(await findCurrentPlanLine()).toHaveTextContent('You are currently on the svod plan.');
    });
  });

  it('discloses that the free plan is ad-supported', async () => {
    renderWithQuery(<BillingPage />);

    expect(await screen.findByText(/Free plan is ad-supported/i)).toBeInTheDocument();
  });
});
