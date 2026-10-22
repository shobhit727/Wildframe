/**
 * Sign-in / sign-up — `/login` and `/signup`, plus the middleware's
 * protected-route gate.
 *
 * The previous version of this file asserted only that some `h1` was visible
 * and accepted a URL regex of `/\/login|\/account/`, which every navigation
 * satisfies. Every assertion below pins a specific field, message, or
 * navigation so a regression in the auth flow fails the test.
 */
import {
  test,
  expect,
  anonymous,
  loggedOut,
  mockGateway,
  VIEWER,
  MOCK_ACCESS_TOKEN,
  MOCK_REFRESH_TOKEN,
} from './fixtures';

const PROTECTED_ROUTES = [
  { path: '/browse', name: 'browse' },
  { path: '/watch/wf-movie-nightfall', name: 'watch' },
  { path: '/my-list', name: 'my list' },
  { path: '/account', name: 'account' },
  { path: '/billing', name: 'billing' },
  { path: '/creator', name: 'creator' },
];

test.describe('Sign in — /login', () => {
  test('renders the sign-in form with labelled credential fields', async ({ page }) => {
    await loggedOut(page);
    await page.goto('/login');

    await expect(page.getByRole('heading', { level: 1, name: 'Sign In' })).toBeVisible();
    await expect(page.getByLabel('Email', { exact: true })).toHaveAttribute('type', 'email');
    await expect(page.getByLabel('Password', { exact: true })).toHaveAttribute('type', 'password');
    await expect(page.getByRole('button', { name: 'Sign In' })).toBeEnabled();
    await expect(page.getByRole('link', { name: 'Sign up now' })).toHaveAttribute(
      'href',
      '/signup'
    );
  });

  test('blocks submission and reports every missing field', async ({ page }) => {
    await loggedOut(page);
    await page.goto('/login');
    await page.getByRole('button', { name: 'Sign In' }).click();

    // Scoped to the form: sonner renders its own role="alert" toast region.
    await expect(page.locator('form').getByRole('alert')).toHaveText([
      'Email is required',
      'Password is required',
    ]);
    // Still on the form: validation must not submit.
    await expect(page).toHaveURL(/\/login$/);
  });

  test('rejects a malformed email and a short password', async ({ page }) => {
    await loggedOut(page);
    await page.goto('/login');

    await page.getByLabel('Email', { exact: true }).fill('not-an-email');
    await page.getByLabel('Password', { exact: true }).fill('abc');
    await page.getByRole('button', { name: 'Sign In' }).click();

    await expect(page.locator('form').getByRole('alert')).toHaveText([
      'Enter a valid email address',
      'Password must be at least 6 characters',
    ]);
  });

  test('reports bad credentials without leaving the form', async ({ page }) => {
    await loggedOut(page);
    // Registered after the catch-all so it wins for the login URL: Playwright
    // consults the most recently added matching handler first.
    await page.route('https://localhost:8000/auth/api/v1/auth/login', (route) =>
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Incorrect email or password' }),
      })
    );

    await page.goto('/login');
    await page.getByLabel('Email', { exact: true }).fill('ada@wildframe.test');
    await page.getByLabel('Password', { exact: true }).fill('correct-horse');
    await page.getByRole('button', { name: 'Sign In' }).click();

    await expect(page.locator('form').getByRole('alert')).toHaveText([
      'Invalid email or password. Please try again.',
    ]);
    await expect(page).toHaveURL(/\/login$/);
  });

  test('confirms a successful sign-in and clears the form errors', async ({ page }) => {
    await loggedOut(page);
    await page.goto('/login');

    await page.getByLabel('Email', { exact: true }).fill(VIEWER.email);
    await page.getByLabel('Password', { exact: true }).fill('correct-horse-battery');
    await page.getByRole('button', { name: 'Sign In' }).click();

    // The success toast is the app's own signal that login() resolved and
    // getMe() returned an identity. The subsequent `router.push('/browse')`
    // is a soft navigation, so src/proxy.ts re-checks the refresh cookie —
    // which this test cannot seed through the intercepted /auth-session route
    // — and bounces it back here. Asserting the redirect would assert the
    // harness, not the app.
    await expect(page.getByText('Welcome back!')).toBeVisible();
    await expect(page.locator('form').getByRole('alert')).toHaveCount(0);
  });

  test('steps into the MFA challenge when the account demands it', async ({ page }) => {
    await loggedOut(page);
    await page.route('https://localhost:8000/auth/api/v1/auth/login', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          requires_mfa: true,
          mfa_challenge: 'e2e-challenge',
          expires_in: 300,
        }),
      })
    );

    await page.goto('/login');
    await page.getByLabel('Email', { exact: true }).fill('mfa@wildframe.test');
    await page.getByLabel('Password', { exact: true }).fill('correct-horse-battery');
    await page.getByRole('button', { name: 'Sign In' }).click();

    await expect(page.getByText('Two-step verification required')).toBeVisible();
    await expect(page.getByLabel('Authenticator or backup code')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Verify' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Back to sign in' })).toBeVisible();

    // And the step is reversible.
    await page.getByRole('button', { name: 'Back to sign in' }).click();
    await expect(page.getByLabel('Password', { exact: true })).toBeVisible();
  });
});

test.describe('Create account — /signup', () => {
  test('renders every field the NIST-style policy needs', async ({ page }) => {
    await loggedOut(page);
    await page.goto('/signup');

    await expect(
      page.getByRole('heading', { level: 1, name: 'Create Account' })
    ).toBeVisible();
    for (const label of [
      'First name',
      'Last name',
      'Email',
      'Password',
      'Confirm password',
    ]) {
      // exact: true, otherwise "Password" also matches "Confirm password".
      await expect(page.getByLabel(label, { exact: true })).toBeVisible();
    }
    await expect(page.getByRole('button', { name: 'Create Account' })).toBeEnabled();
    await expect(page.getByRole('link', { name: 'Sign in', exact: true })).toHaveAttribute(
      'href',
      '/login'
    );
  });

  test('enforces the 12-character password policy and the confirmation match', async ({
    page,
  }) => {
    await loggedOut(page);
    await page.goto('/signup');

    await page.getByLabel('First name', { exact: true }).fill('Ada');
    await page.getByLabel('Last name', { exact: true }).fill('Lovelace');
    await page.getByLabel('Email', { exact: true }).fill('ada@wildframe.test');
    await page.getByLabel('Password', { exact: true }).fill('Short1!');
    await page.getByLabel('Confirm password', { exact: true }).fill('Short1!');
    await page.getByRole('button', { name: 'Create Account' }).click();

    await expect(page.locator('form').getByRole('alert')).toHaveText([
      'Password must be at least 12 characters',
    ]);

    await page.getByLabel('Password', { exact: true }).fill('longenoughpass1!');
    await page.getByLabel('Confirm password', { exact: true }).fill('longenoughpass2!');
    await page.getByRole('button', { name: 'Create Account' }).click();

    await expect(page.locator('form').getByRole('alert')).toHaveText([
      'Passwords do not match',
    ]);
  });

  test('rejects a password with fewer than two character classes', async ({ page }) => {
    await loggedOut(page);
    await page.goto('/signup');

    await page.getByLabel('First name', { exact: true }).fill('Ada');
    await page.getByLabel('Last name', { exact: true }).fill('Lovelace');
    await page.getByLabel('Email', { exact: true }).fill('ada@wildframe.test');
    await page.getByLabel('Password', { exact: true }).fill('alllowercaseletters');
    await page.getByLabel('Confirm password', { exact: true }).fill('alllowercaseletters');
    await page.getByRole('button', { name: 'Create Account' }).click();

    await expect(page.locator('form').getByRole('alert')).toHaveText([
      'Password must mix at least two of: letters, numbers, symbols',
    ]);
  });

  test('confirms a successful registration and sends the user to sign in', async ({
    page,
  }) => {
    await loggedOut(page);
    await page.goto('/signup');

    await page.getByLabel('First name', { exact: true }).fill('Ada');
    await page.getByLabel('Last name', { exact: true }).fill('Lovelace');
    await page.getByLabel('Email', { exact: true }).fill('ada@wildframe.test');
    await page.getByLabel('Password', { exact: true }).fill('longenoughpass1!');
    await page.getByLabel('Confirm password', { exact: true }).fill('longenoughpass1!');
    await page.getByRole('button', { name: 'Create Account' }).click();

    await expect(page.getByText('Account created! Please sign in.')).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Sign In' })).toBeVisible();
  });

  test('surfaces a duplicate-account conflict on the email field', async ({ page }) => {
    await loggedOut(page);
    await page.route('https://localhost:8000/auth/api/v1/auth/register', (route) =>
      route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'User already exists' }),
      })
    );

    await page.goto('/signup');
    await page.getByLabel('First name', { exact: true }).fill('Ada');
    await page.getByLabel('Last name', { exact: true }).fill('Lovelace');
    await page.getByLabel('Email', { exact: true }).fill('ada@wildframe.test');
    await page.getByLabel('Password', { exact: true }).fill('longenoughpass1!');
    await page.getByLabel('Confirm password', { exact: true }).fill('longenoughpass1!');
    await page.getByRole('button', { name: 'Create Account' }).click();

    await expect(page.locator('form').getByRole('alert')).toHaveText([
      'An account with this email already exists. Try signing in.',
    ]);
  });
});

test.describe('Protected route gate (src/proxy.ts)', () => {
  for (const route of PROTECTED_ROUTES) {
    test(`bounces an anonymous visitor from ${route.path} to the sign-in form`, async ({
      page,
    }) => {
      await anonymous(page);
      await page.goto(route.path);

      await expect(page).toHaveURL(/\/login$/);
      await expect(
        page.getByRole('heading', { level: 1, name: 'Sign In' })
      ).toBeVisible();
      // Not a 404 — the guard ran and the route itself exists.
      await expect(page.getByText('404')).toHaveCount(0);
    });
  }

  test('leaves public routes reachable without a session', async ({ page }) => {
    await anonymous(page);

    for (const path of ['/', '/login', '/signup']) {
      await page.goto(path);
      await expect(page).toHaveURL(new RegExp(`${path}$`));
    }
  });

  test('lets a signed-in visitor through to a protected route', async ({ page }) => {
    await mockGateway(page, { user: VIEWER });
    await page.goto('/account');

    await expect(page.getByRole('heading', { level: 1, name: 'Account' })).toBeVisible();
  });
});

test.describe('Session bootstrap', () => {
  test('resolves to a logged-out state when there is no session', async ({ page }) => {
    // The app must not hang on the Providers auth gate when /auth-session 401s.
    await page.route('**/auth-session', (route) =>
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'no_session' }),
      })
    );

    await page.goto('/');
    await expect(
      page.getByRole('heading', { level: 1, name: 'Stories that pull you in.' })
    ).toBeVisible();
  });

  test('never exposes a token to localStorage', async ({ page }) => {
    await mockGateway(page, { user: VIEWER });
    await page.goto('/browse');
    await expect(
      page.getByRole('heading', { level: 2, name: 'Trending Now' })
    ).toBeVisible();

    const leaked = await page.evaluate(() => {
      const keys = Object.keys(window.localStorage);
      return {
        keys,
        values: keys.map((k) => window.localStorage.getItem(k) ?? ''),
      };
    });
    const blob = JSON.stringify(leaked);
    expect(blob).not.toContain(MOCK_ACCESS_TOKEN);
    expect(blob).not.toContain(MOCK_REFRESH_TOKEN);
    expect(leaked.keys).not.toContain('accessToken');
    expect(leaked.keys).not.toContain('refreshToken');
    expect(leaked.keys).not.toContain('user');
  });
});
