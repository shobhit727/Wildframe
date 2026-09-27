/**
 * Creator workspace — `/creator`.
 *
 * Note on access control: `/creator` is absent from the `protectedRoutes`
 * array in `src/proxy.ts`, so unlike `/browse` it renders for an anonymous
 * visitor and exposes the full catalog. See the bug report.
 */
import {
  test,
  expect,
  signedIn,
  mockGateway,
  MOVIE_ID,
  MOVIES,
  SHOWS,
} from './fixtures';

const CATALOG_TOTAL = MOVIES.length + SHOWS.length;

test.describe('/creator', () => {
  test('renders the studio header and roadmap sections', async ({ page }) => {
    await mockGateway(page, {});
    await page.goto('/creator');

    await expect(page.getByText('Creator Studio')).toBeVisible();
    await expect(
      page.getByRole('heading', { level: 1, name: 'Your work, in one place.' })
    ).toBeVisible();
    await expect(
      page.getByRole('heading', { level: 2, name: 'Content slate' })
    ).toBeVisible();
    await expect(page.getByText('Read-only preview')).toBeVisible();
  });

  test('is reachable without a session — unlike the gated catalog', async ({ page }) => {
    await mockGateway(page, {});
    await page.goto('/creator');

    // No redirect, and the catalog is disclosed to an anonymous visitor.
    await expect(page).toHaveURL(/\/creator$/);
    await expect(
      page.getByRole('heading', { level: 1, name: 'Your work, in one place.' })
    ).toBeVisible();
    await expect(page.locator(`a[href="/watch/${MOVIE_ID}"]`)).toHaveCount(1);
  });

  test('counts the catalog by type', async ({ page }) => {
    await mockGateway(page, {});
    await page.goto('/creator');

    await expect(page.getByText('Published slate', { exact: true })).toBeVisible();
    await expect(page.getByText('Movies', { exact: true })).toBeVisible();
    await expect(page.getByText('Series', { exact: true })).toBeVisible();

    const metrics = page.locator('article');
    await expect(metrics.nth(0)).toContainText(String(CATALOG_TOTAL));
    await expect(metrics.nth(1)).toContainText(String(MOVIES.length));
    await expect(metrics.nth(2)).toContainText(String(SHOWS.length));
  });

  test('links every slate card to its watch route', async ({ page }) => {
    await mockGateway(page, {});
    await page.goto('/creator');

    for (const movie of MOVIES) {
      const card = page.locator(`a[href="/watch/${movie.id}"]`);
      await expect(card).toHaveCount(1);
      await expect(card).toContainText(movie.title);
    }
  });

  test('personalises the greeting for a signed-in creator', async ({ page }) => {
    await signedIn(page);
    await page.goto('/creator');

    await expect(page.getByText('Welcome, Ada')).toBeVisible();
  });

  test('offers navigation to the platform and marks publishing as unavailable', async ({
    page,
  }) => {
    await mockGateway(page, {});
    await page.goto('/creator');

    await expect(page.getByRole('link', { name: 'View platform' })).toHaveAttribute(
      'href',
      '/browse'
    );
    const upload = page.getByRole('button', { name: 'Upload content — coming soon' });
    await expect(upload).toBeDisabled();
  });

  test('reports a catalog outage instead of rendering an empty grid', async ({ page }) => {
    await mockGateway(page, { failAll: true });
    await page.goto('/creator');

    await expect(
      page.getByText('The catalog could not be loaded. Check the API gateway and try again.')
    ).toBeVisible();
  });

  test('shows the empty-catalog message when the gateway returns nothing', async ({
    page,
  }) => {
    await signedIn(page, { content: [] });
    await page.goto('/creator');

    await expect(
      page.getByText('No published content is available yet.')
    ).toBeVisible();
  });

  test('describes the three roadmap areas', async ({ page }) => {
    await mockGateway(page, {});
    await page.goto('/creator');

    await expect(page.getByRole('heading', { level: 3, name: 'Content' })).toBeVisible();
    await expect(page.getByRole('heading', { level: 3, name: 'Analytics' })).toBeVisible();
    await expect(page.getByRole('heading', { level: 3, name: 'Publishing' })).toBeVisible();
  });
});
