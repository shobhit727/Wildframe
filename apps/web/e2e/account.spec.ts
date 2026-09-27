/**
 * Account settings — `/account`.
 *
 * Four tabs (Profile, Subscription, Preferences, Devices), each backed by a
 * different user-service / billing-service endpoint.
 */
import {
  test,
  expect,
  anonymous,
  signedIn,
  expectRedirectedToLogin,
  VIEWER,
} from './fixtures';

test.describe('/account — gate', () => {
  test('sends an anonymous visitor to the sign-in form', async ({ page }) => {
    await anonymous(page);
    await page.goto('/account');
    await expectRedirectedToLogin(page);
  });
});

test.describe('/account — profile tab', () => {
  test.beforeEach(async ({ page }) => {
    await signedIn(page);
    await page.goto('/account');
    await expect(page.getByRole('heading', { level: 1, name: 'Account' })).toBeVisible();
  });

  test('shows the signed-in identity and the four tab triggers', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 2, name: 'Profile Information' })
    ).toBeVisible();
    await expect(page.getByText('Ada Lovelace', { exact: true })).toBeVisible();
    await expect(page.getByText(VIEWER.email)).toBeVisible();
    await expect(page.getByText('✓ verified')).toBeVisible();

    for (const tab of ['Profile', 'Subscription', 'Preferences', 'Devices']) {
      await expect(page.getByRole('tab', { name: tab })).toBeVisible();
    }
  });

  test('renders the profile metadata grid from user-service', async ({ page }) => {
    await expect(page.getByText('Country', { exact: true })).toBeVisible();
    await expect(page.getByText('New Zealand', { exact: true })).toBeVisible();
    await expect(page.getByText('Language', { exact: true })).toBeVisible();
    await expect(page.getByText('Member since', { exact: true })).toBeVisible();
    await expect(page.getByText('Profile completion', { exact: true })).toBeVisible();
    await expect(page.getByText('72%', { exact: true })).toBeVisible();
  });

  test('switches into edit mode and saves the profile', async ({ page }) => {
    const save = page.getByRole('button', { name: 'Save' });
    await expect(save).toHaveCount(0);

    await page.getByRole('button', { name: 'Edit' }).click();
    await expect(save).toBeVisible();
    await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible();

    await save.click();
    await expect(page.getByText('Profile updated')).toBeVisible();
  });
});

test.describe('/account — subscription tab', () => {
  test.beforeEach(async ({ page }) => {
    await signedIn(page);
    await page.goto('/account');
    await page.getByRole('tab', { name: 'Subscription' }).click();
  });

  test('shows the active tier, price and plan benefits', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 2, name: 'Subscription' })
    ).toBeVisible();
    // `tier` is uppercased with CSS, so the DOM text stays lowercase.
    await expect(page.getByText('svod', { exact: true })).toBeVisible();
    await expect(page.getByText('active', { exact: true })).toBeVisible();
    await expect(page.getByText('$7.99', { exact: false })).toBeVisible();
    await expect(page.getByText('Ad-free streaming', { exact: true })).toBeVisible();
    await expect(page.getByText('Download offline', { exact: true })).toBeVisible();
  });

  test('links onward to the billing plan picker', async ({ page }) => {
    const changePlan = page.getByRole('link', { name: /Change plan/ });
    await expect(changePlan).toHaveAttribute('href', '/billing');
  });
});

test.describe('/account — preferences tab', () => {
  test('lists the five toggles and flips one through the API', async ({ page }) => {
    await signedIn(page);
    await page.goto('/account');
    await page.getByRole('tab', { name: 'Preferences' }).click();

    await expect(
      page.getByRole('heading', { level: 2, name: 'Preferences' })
    ).toBeVisible();
    for (const label of [
      'Autoplay next episode',
      'Auto-advance',
      'Show subtitles',
      'Allow mature content',
      'Email notifications',
    ]) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: `Toggle ${label}` })).toBeVisible();
    }

    // `autoplay_next_episode` is false in the fixture, so one click enables it.
    await page.getByRole('button', { name: 'Toggle Auto-advance' }).click();
    await expect(page.getByText('Auto-advance enabled')).toBeVisible();
  });
});

test.describe('/account — devices tab', () => {
  test('lists the registered device with its trust badges', async ({ page }) => {
    await signedIn(page);
    await page.goto('/account');
    await page.getByRole('tab', { name: 'Devices' }).click();

    await expect(page.getByRole('heading', { level: 2, name: 'Devices' })).toBeVisible();
    // The device name shares a <p> with the Active/Trusted badges.
    await expect(page.getByText('Living Room TV')).toBeVisible();
    await expect(page.getByText('Active', { exact: true })).toBeVisible();
    await expect(page.getByText('Trusted', { exact: true })).toBeVisible();
    // Active devices are not removable.
    await expect(page.getByRole('button', { name: 'Remove' })).toHaveCount(0);
  });

  test('shows the empty-state copy when no devices are registered', async ({ page }) => {
    await signedIn(page, { user: { ...VIEWER, id: 'e2e-user-deviceless' } });
    // Registered after the catch-all so this override wins.
    await page.route(
      'https://localhost:8000/users/api/v1/devices/**',
      (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/account');
    await page.getByRole('tab', { name: 'Devices' }).click();

    await expect(
      page.getByText('No devices registered yet. Devices are added when you stream.')
    ).toBeVisible();
  });
});
