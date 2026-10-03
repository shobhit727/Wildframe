/**
 * Admin console — `/admin` and its five sub-routes.
 *
 * `/admin` is not in `protectedRoutes` in `src/proxy.ts`; access control is
 * enforced entirely by the `AdminGate` client component in
 * `src/app/admin/layout.tsx`. These specs cover both the gate's two redirect
 * branches and the six pages behind it.
 */
import { test, expect, anonymous, signedIn, signedInAsAdmin, ADMIN, ADMIN_STATS } from './fixtures';

const ADMIN_ROUTES = [
  { path: '/admin', heading: 'Command Center' },
  { path: '/admin/users', heading: 'Users' },
  { path: '/admin/flags', heading: 'Content Flags' },
  { path: '/admin/alerts', heading: 'System Alerts' },
  { path: '/admin/config', heading: 'System Config' },
  { path: '/admin/audit', heading: 'Audit Log' },
];

test.describe('/admin — access gate', () => {
  for (const route of ADMIN_ROUTES) {
    test(`sends an anonymous visitor from ${route.path} to the sign-in form`, async ({
      page,
    }) => {
      await anonymous(page);
      await page.goto(route.path);

      await expect(page.getByText('Redirecting to sign in…')).toBeVisible();
      await expect(page).toHaveURL(/\/login$/);
      await expect(
        page.getByRole('heading', { level: 1, name: 'Sign In' })
      ).toBeVisible();
    });

    test(`bounces a signed-in non-admin from ${route.path} to account`, async ({ page }) => {
      await signedIn(page);
      await page.goto(route.path);

      await expect(page.getByText('Checking permissions…')).toBeVisible();
      await expect(page).toHaveURL(/\/account$/);
      await expect(page.getByRole('heading', { level: 1, name: 'Account' })).toBeVisible();
    });
  }

  test('never leaks admin data to a signed-in non-admin', async ({ page }) => {
    const calls: string[] = [];
    page.on('request', (r) => {
      if (r.url().includes('localhost:8000/admin/')) calls.push(r.url());
    });

    await signedIn(page);
    await page.goto('/admin/users');
    await expect(page).toHaveURL(/\/account$/);

    // AdminGate never mounts the page component, so no admin endpoint is hit.
    expect(calls).toEqual([]);
  });
});

test.describe('/admin — shell', () => {
  test.beforeEach(async ({ page }) => {
    await signedInAsAdmin(page);
    await page.goto('/admin');
    await expect(
      page.getByRole('heading', { level: 1, name: 'Command Center' })
    ).toBeVisible();
  });

  test('shows the console identity and all six navigation links', async ({ page }) => {
    await expect(page.getByText('Admin Console')).toBeVisible();
    await expect(page.getByText('Grace Hopper', { exact: true })).toBeVisible();
    await expect(page.getByText(ADMIN.email)).toBeVisible();

    for (const [name, href] of [
      ['Dashboard', '/admin'],
      ['Users', '/admin/users'],
      ['Content Flags', '/admin/flags'],
      ['Alerts', '/admin/alerts'],
      ['System Config', '/admin/config'],
      ['Audit Log', '/admin/audit'],
    ] as const) {
      await expect(page.getByRole('link', { name, exact: true })).toHaveAttribute('href', href);
    }
    await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
  });

  test('renders the four operational stat cards from the stats endpoint', async ({ page }) => {
    await expect(page.getByText('Total Users', { exact: true })).toBeVisible();
    await expect(page.getByText('Active Streams')).toBeVisible();
    await expect(page.getByText('Open Flags', { exact: true })).toBeVisible();
    await expect(page.getByText('MRR', { exact: true })).toBeVisible();

    // Intl-formatted counts, not raw numbers.
    await expect(page.getByText(ADMIN_STATS.total_users.toLocaleString('en-US'))).toBeVisible();
    await expect(
      page.getByText(`${ADMIN_STATS.active_users.toLocaleString('en-US')} active`)
    ).toBeVisible();
    await expect(page.getByText(String(ADMIN_STATS.flagged_content))).toBeVisible();
    await expect(
      page.getByText(`${ADMIN_STATS.active_alerts} alerts`)
    ).toBeVisible();
  });

  test('lists recent registrations and a labelled traffic chart', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 2, name: 'Traffic overview' })
    ).toBeVisible();
    await expect(
      page.getByRole('heading', { level: 2, name: 'New users' })
    ).toBeVisible();
    await expect(
      page.getByRole('img', { name: /Relative traffic activity over fourteen days/ })
    ).toBeVisible();

    await expect(page.getByText('Ada Lovelace', { exact: true })).toBeVisible();
    await expect(page.getByText('ada@wildframe.test')).toBeVisible();
  });

  test('navigates between every admin page from the sidebar', async ({ page }) => {
    for (const route of ADMIN_ROUTES.slice(1)) {
      await page.locator(`aside a[href="${route.path}"]`).click();
      await expect(page).toHaveURL(new RegExp(`${route.path}$`));
      await expect(
        page.getByRole('heading', { level: 1, name: route.heading })
      ).toBeVisible();
    }
  });
});

test.describe('/admin/users', () => {
  test.beforeEach(async ({ page }) => {
    await signedInAsAdmin(page);
    await page.goto('/admin/users');
  });

  test('lists users with their moderation status', async ({ page }) => {
    await expect(page.getByRole('heading', { level: 1, name: 'Users' })).toBeVisible();
    await expect(
      page.getByText('Manage platform users and moderation status.')
    ).toBeVisible();

    await expect(page.getByText('Ada Lovelace', { exact: true })).toBeVisible();
    await expect(page.getByText('ada@wildframe.test')).toBeVisible();
    await expect(page.getByText('Linus Torvalds', { exact: true })).toBeVisible();
    await expect(page.getByText('suspended', { exact: true })).toBeVisible();
    await expect(page.getByText('2 users', { exact: true })).toBeVisible();
  });

  test('filters the table with the search field', async ({ page }) => {
    const search = page.getByPlaceholder('Search by name, email, or ID…');
    await search.fill('Linus');

    await expect(page.getByText('Linus Torvalds', { exact: true })).toBeVisible();
    await expect(page.getByText('Ada Lovelace', { exact: true })).toHaveCount(0);
    await expect(page.getByText('1 user', { exact: true })).toBeVisible();

    await search.fill('');
    await expect(page.getByText('Ada Lovelace', { exact: true })).toBeVisible();
  });

  test('exposes the status filter chips and per-row actions', async ({ page }) => {
    for (const filter of ['All', 'Active', 'Suspended', 'Banned']) {
      await expect(page.getByRole('button', { name: filter, exact: true })).toBeVisible();
    }
    await expect(page.getByRole('button', { name: 'Row actions' })).toHaveCount(2);
  });

  test('shows the empty state when no users match', async ({ page }) => {
    await page.getByPlaceholder('Search by name, email, or ID…').fill('nobody-here');
    await expect(
      page.getByRole('heading', { level: 3, name: 'No users found' })
    ).toBeVisible();
    await expect(page.getByText('0 users', { exact: true })).toBeVisible();
  });
});

test.describe('/admin/flags', () => {
  test.beforeEach(async ({ page }) => {
    await signedInAsAdmin(page);
    await page.goto('/admin/flags');
  });

  test('lists flagged content with resolve and dismiss actions', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 1, name: 'Content Flags' })
    ).toBeVisible();
    await expect(page.getByText('Explicit content', { exact: true })).toBeVisible();
    await expect(page.getByText('Copyright claim', { exact: true })).toBeVisible();
    await expect(page.getByText('2 flags', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Resolve' })).toHaveCount(2);
    await expect(page.getByRole('button', { name: 'Dismiss' })).toHaveCount(2);
  });

  test('confirms before resolving and warns before removing content', async ({ page }) => {
    await page.getByRole('button', { name: 'Resolve' }).first().click();
    await expect(page.getByText('Resolve flag', { exact: true })).toBeVisible();
    await expect(
      page.getByText(/will be set to "active"/)
    ).toBeVisible();
    await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible();

    await page.getByRole('button', { name: 'Cancel' }).click();
    await page.getByRole('button', { name: 'Dismiss' }).first().click();
    await expect(
      page.getByText('Dismiss flag & remove content', { exact: true })
    ).toBeVisible();
  });

  test('filters flags by content id', async ({ page }) => {
    await page.getByPlaceholder('Search by content ID, type, or reason…').fill('Copyright');
    await expect(page.getByText('Copyright claim', { exact: true })).toBeVisible();
    await expect(page.getByText('Explicit content', { exact: true })).toHaveCount(0);
    await expect(page.getByText('1 flag', { exact: true })).toBeVisible();
  });
});

test.describe('/admin/alerts', () => {
  test.beforeEach(async ({ page }) => {
    await signedInAsAdmin(page);
    await page.goto('/admin/alerts');
  });

  test('lists alerts with severity, service and acknowledgement state', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 1, name: 'System Alerts' })
    ).toBeVisible();
    await expect(page.getByText('high-latency', { exact: true })).toBeVisible();
    await expect(
      page.getByText('streaming-service p95 above 900ms', { exact: true })
    ).toBeVisible();
    await expect(page.getByText('streaming-service', { exact: true })).toBeVisible();
    await expect(page.getByText('warning', { exact: true })).toBeVisible();
    await expect(page.getByText('critical', { exact: true })).toBeVisible();
  });

  test('only offers acknowledgement for unacknowledged alerts', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Acknowledge' })).toHaveCount(1);
    await expect(page.getByText(`acknowledged by ${ADMIN.id}`)).toBeVisible();
  });

  test('keeps the create button inert until the form is complete', async ({ page }) => {
    await page.getByRole('button', { name: 'New alert' }).click();
    await expect(
      page.getByText('Create system alert', { exact: true })
    ).toBeVisible();

    const create = page.getByRole('button', { name: 'Create alert' });
    await expect(create).toBeDisabled();

    await page.getByPlaceholder('e.g. high-latency').fill('disk-pressure');
    await page.getByPlaceholder('e.g. streaming-service').fill('media-pipeline');
    await page.getByPlaceholder('Describe the issue…').fill('Volume at 91%');
    await expect(create).toBeEnabled();
  });
});

test.describe('/admin/config', () => {
  test.beforeEach(async ({ page }) => {
    await signedInAsAdmin(page);
    await page.goto('/admin/config');
  });

  test('renders the key/value config table', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 1, name: 'System Config' })
    ).toBeVisible();
    await expect(page.getByText('rate_limit.rps', { exact: true })).toBeVisible();
    await expect(page.getByText('120', { exact: true })).toBeVisible();
    await expect(page.getByText('integer', { exact: true })).toBeVisible();
    await expect(
      page.getByText('Gateway-wide requests per second', { exact: true })
    ).toBeVisible();
    await expect(page.getByRole('button', { name: 'Edit' })).toHaveCount(2);
  });

  test('opens an edit drawer seeded with the stored value', async ({ page }) => {
    await page.getByRole('button', { name: 'Edit' }).first().click();
    await expect(page.getByText('Edit "rate_limit.rps"', { exact: true })).toBeVisible();
    // The drawer's Value control is the only textarea.
    await expect(page.locator('textarea')).toHaveValue('120');
    // The key is immutable while editing an existing entry.
    await expect(page.getByPlaceholder('feature.flag_name')).toBeDisabled();
  });

  test('filters rows by key', async ({ page }) => {
    await page.getByPlaceholder('Search by key, value, or description…').fill('preview_url');
    await expect(
      page.getByText('streaming.preview_url', { exact: true })
    ).toBeVisible();
    await expect(page.getByText('rate_limit.rps', { exact: true })).toHaveCount(0);
  });

  test('shows the empty state when nothing matches', async ({ page }) => {
    await page.getByPlaceholder('Search by key, value, or description…').fill('zzz');
    await expect(
      page.getByRole('heading', { level: 3, name: 'No config entries' })
    ).toBeVisible();
  });
});

test.describe('/admin/audit', () => {
  test.beforeEach(async ({ page }) => {
    await signedInAsAdmin(page);
    await page.goto('/admin/audit');
  });

  test('renders the page header and the filter controls', async ({ page }) => {
    await expect(page.getByRole('heading', { level: 1, name: 'Audit Log' })).toBeVisible();
    await expect(
      page.getByText('Immutable record of privileged actions across the platform.')
    ).toBeVisible();
    await expect(page.getByPlaceholder('Search admin, action, resource…')).toBeVisible();
    await expect(page.getByPlaceholder('Filter by admin…')).toBeVisible();
    await expect(page.getByRole('combobox')).toBeVisible();
  });

  test('starts empty because listAuditLogs returns [] with no filter', async ({ page }) => {
    // `listAuditLogs` in src/api/admin.ts only issues a request when
    // `admin_id` or a resource pair is supplied, and otherwise returns [].
    // So the audit log never loads on arrival — an admin has to already know
    // an admin id to see anything. Pinned here as current behaviour; see the
    // bug report.
    await expect(
      page.getByRole('heading', { level: 3, name: 'No audit entries' })
    ).toBeVisible();
    await expect(page.getByText('Actions by admins will be recorded here.')).toBeVisible();
    await expect(page.getByText('0 entries', { exact: true })).toBeVisible();
  });

  test('surfaces entries with action, resource and diff once an admin id is given', async ({
    page,
  }) => {
    await page.getByPlaceholder('Filter by admin…').fill(ADMIN.id);

    await expect(page.getByText('user_moderation', { exact: true })).toBeVisible();
    await expect(page.getByText('set_config', { exact: true })).toBeVisible();
    await expect(page.getByText('{"status":"suspended"}', { exact: true })).toBeVisible();
    await expect(page.getByText('{"value":"120"}', { exact: true })).toBeVisible();
    await expect(page.getByText('2 entries', { exact: true })).toBeVisible();
  });

  test('filters by action type', async ({ page }) => {
    await page.getByPlaceholder('Filter by admin…').fill(ADMIN.id);
    // The admin `Field` wrapper renders a <label> with no `htmlFor` and the
    // control has no `id`, so the control is reachable by role only.
    // See the accessibility bug filed against components/admin/fields.tsx.
    await page.getByRole('combobox').selectOption('set_config');

    await expect(page.getByText('set_config', { exact: true })).toBeVisible();
    await expect(page.getByText('user_moderation', { exact: true })).toHaveCount(0);
    await expect(page.getByText('1 entry', { exact: true })).toBeVisible();
  });

  test('narrows the results with the free-text search', async ({ page }) => {
    await page.getByPlaceholder('Filter by admin…').fill(ADMIN.id);
    await page.getByPlaceholder('Search admin, action, resource…').fill('Copyright');

    await expect(
      page.getByRole('heading', { level: 3, name: 'No audit entries' })
    ).toBeVisible();
  });
});
