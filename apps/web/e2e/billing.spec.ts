/**
 * Plan selection — `/billing`.
 *
 * Renamed from `subscription.spec.ts`, which visited `/subscription` (a route
 * that does not exist) and asserted only that some `h1` was visible. The
 * not-found page's `<h1>404</h1>` satisfied that assertion.
 */
import {
  test,
  expect,
  anonymous,
  signedIn,
  expectRedirectedToLogin,
} from './fixtures';

test.describe('/billing — gate', () => {
  test('sends an anonymous visitor to the sign-in form', async ({ page }) => {
    await anonymous(page);
    await page.goto('/billing');
    await expectRedirectedToLogin(page);
  });
});

test.describe('/billing — plan grid', () => {
  test.beforeEach(async ({ page }) => {
    await signedIn(page);
    await page.goto('/billing');
    await expect(page.getByRole('heading', { level: 1, name: 'Choose your plan' })).toBeVisible();
  });

  test('renders the three plans with their headline prices', async ({ page }) => {
    for (const plan of ['Free', 'Premium', 'Pay-Per-View']) {
      await expect(page.getByRole('heading', { level: 2, name: plan })).toBeVisible();
    }
    await expect(page.getByText('$0', { exact: true })).toBeVisible();
    await expect(page.getByText('$7.99', { exact: false })).toBeVisible();
    await expect(page.getByText('From $3.99', { exact: true })).toBeVisible();
    await expect(page.getByText('/mo', { exact: true })).toHaveCount(1);
  });

  test('badges Premium as the most popular option', async ({ page }) => {
    await expect(page.getByText('Most popular')).toBeVisible();
    await expect(page.getByText('Ad-free, on every screen')).toBeVisible();
  });

  test('lists each plan’s benefits', async ({ page }) => {
    for (const benefit of [
      'Unlimited movies & shows',
      'SD quality',
      '1 stream',
      'Watch on 1 device',
      'Ad-free streaming',
      'Full HD quality',
      '2 streams',
      'Download offline',
      'Cancel anytime',
      'Rent or buy',
      'Latest blockbusters',
      '48h rental window',
    ]) {
      await expect(page.getByText(benefit, { exact: true })).toBeVisible();
    }
  });

  test('marks the subscribed tier as current and disables its button', async ({ page }) => {
    await expect(
      page.getByText('You are currently on the', { exact: false })
    ).toBeVisible();
    // The tier span is uppercased via CSS, so the DOM text stays lowercase.
    await expect(page.getByText('svod', { exact: true })).toBeVisible();

    const currentPlanButton = page.getByRole('button', { name: 'Current plan' });
    await expect(currentPlanButton).toBeVisible();
    await expect(currentPlanButton).toBeDisabled();

    // Non-current tiers still offer a change.
    await expect(page.getByRole('button', { name: 'Choose' })).toBeEnabled();
    await expect(
      page.getByRole('button', { name: 'Switch to Free' })
    ).toBeEnabled();
  });

  test('offers cancellation for a paid plan and states the access window', async ({ page }) => {
    await expect(page.getByText('Cancel Premium')).toBeVisible();
    await expect(
      page.getByText(
        'Your access stays active until the end of the current period, then you return to the free plan.'
      )
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Cancel subscription' })
    ).toBeEnabled();
  });

  test('discloses payment handling and the free-tier ad support', async ({ page }) => {
    await expect(
      page.getByText('Payments are processed securely. Free plan is ad-supported.')
    ).toBeVisible();
  });
});

test.describe('/billing — plan changes', () => {
  test('confirms a switch to pay-per-view', async ({ page }) => {
    await signedIn(page);
    await page.goto('/billing');

    const payPerView = page
      .locator('div')
      .filter({ has: page.getByRole('heading', { level: 2, name: 'Pay-Per-View' }) })
      .last();
    await payPerView.getByRole('button', { name: 'Choose' }).click();

    await expect(page.getByText('Pay-per-view enabled')).toBeVisible();
  });

  test('hides the cancel section for a user on the free plan', async ({ page }) => {
    await signedIn(page);
    // Registered after the catch-all so this override wins.
    await page.route(
      'https://localhost:8000/billing/api/v1/billing/subscription/**',
      (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ tier: 'avod', subscription_status: 'inactive' }),
        })
    );
    await page.goto('/billing');

    await expect(page.getByText('You are currently on the', { exact: false })).toBeVisible();
    await expect(page.getByText('avod', { exact: true })).toBeVisible();
    // avod is the free tier, so there is nothing to cancel.
    await expect(page.getByRole('button', { name: 'Cancel subscription' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Current plan' })).toHaveCount(1);
  });

  test('falls back to generic copy while the subscription is still loading', async ({
    page,
  }) => {
    await signedIn(page);
    // Hold the subscription request, then abort it, so the pre-load branch is
    // observable without leaving a request pending at context teardown.
    await page.route(
      'https://localhost:8000/billing/api/v1/billing/subscription/**',
      async (route) => {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        await route.abort();
      }
    );
    await page.goto('/billing');

    await expect(
      page.getByText('Pick the plan that fits how you watch.')
    ).toBeVisible();
  });
});
