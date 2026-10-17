/**
 * Content discovery — `/browse`, which is also the app's only search surface.
 *
 * The previous `content.spec.ts` visited `/content/1` and `/search`, neither of
 * which exists, and asserted only that some `h1` was visible. Both requests
 * render `src/app/not-found.tsx`, whose `<h1>404</h1>` satisfied the
 * assertion, so the file proved nothing. These specs target the routes that
 * actually exist and assert specific catalog content.
 */
import {
  test,
  expect,
  anonymous,
  signedIn,
  expectRedirectedToLogin,
  MOVIE_ID,
  SHOW_ID,
} from './fixtures';

test.describe('/browse — gate', () => {
  test('sends an anonymous visitor to the sign-in form', async ({ page }) => {
    await anonymous(page);
    await page.goto('/browse');
    await expectRedirectedToLogin(page);
  });
});

test.describe('/browse — rows and hero', () => {
  test.beforeEach(async ({ page }) => {
    await signedIn(page);
    await page.goto('/browse');
  });

  test('renders the hero for the first trending title with its match score', async ({
    page,
  }) => {
    // The hero takes the first item of the trending list and owns the only
    // h1 on the page.
    await expect(
      page.getByRole('heading', { level: 1, name: 'Nightfall Protocol' })
    ).toBeVisible();
    await expect(page.getByText('84% Match').first()).toBeVisible();
    // Hero-only marker; the card grid does not render it.
    await expect(page.getByText('ORIGINAL FILM')).toBeVisible();
    await expect(
      page.getByText('A courier races a dying city to hand off a single reel of film.')
    ).toBeVisible();
  });

  test('renders the Trending Now, Popular Movies and TV Shows rows', async ({ page }) => {
    for (const row of ['Trending Now', 'Popular Movies', 'TV Shows']) {
      await expect(page.getByRole('heading', { level: 2, name: row, exact: true })).toBeVisible();
    }
  });

  test('groups the catalog into a genre row once a genre has three titles', async ({
    page,
  }) => {
    // Thriller has three fixtures; Drama and Sci-Fi have one each and are
    // correctly suppressed (browse filters genres with < 3 items).
    await expect(page.getByRole('heading', { level: 2, name: 'Thriller' })).toBeVisible();
    await expect(page.getByRole('heading', { level: 2, name: 'Drama' })).toHaveCount(0);
    await expect(page.getByRole('heading', { level: 2, name: 'Sci-Fi' })).toHaveCount(0);
  });

  test('links every movie and show card to its watch route', async ({ page }) => {
    await expect(page.locator(`a[href="/watch/${MOVIE_ID}"]`).first()).toBeVisible();
    await expect(page.locator(`a[href="/watch/${SHOW_ID}"]`).first()).toBeVisible();
    await expect(
      page.locator(`a[href="/watch/wf-movie-lantern"]`).first()
    ).toBeVisible();
  });

  test('exposes the signed-in identity and the My List nav link', async ({ page }) => {
    await expect(page.getByRole('link', { name: 'Home' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'My List' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Search' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Notifications' })).toBeVisible();
  });
});

test.describe('/browse — search', () => {
  test('narrows the catalog to matching titles', async ({ page }) => {
    await signedIn(page);
    await page.goto('/browse');
    await expect(
      page.getByRole('heading', { level: 2, name: 'Trending Now' })
    ).toBeVisible();

    await page.getByRole('button', { name: 'Search' }).click();
    const input = page.getByRole('textbox', { name: 'Search titles, people, or genres' });
    await expect(input).toBeFocused();
    await input.fill('Lantern');

    await expect(
      page.getByRole('heading', { level: 2, name: 'Results for “Lantern”' })
    ).toBeVisible();
    await expect(page.locator(`a[href="/watch/wf-movie-lantern"]`)).toBeVisible();
    // Only the match survives the filter.
    await expect(page.locator(`a[href="/watch/${MOVIE_ID}"]`)).toHaveCount(0);
    // The browse rows are replaced by the results view.
    await expect(
      page.getByRole('heading', { level: 2, name: 'Trending Now' })
    ).toHaveCount(0);
  });

  test('matches on description text, not just the title', async ({ page }) => {
    await signedIn(page);
    await page.goto('/browse');
    await page.getByRole('button', { name: 'Search' }).click();
    await page
      .getByRole('textbox', { name: 'Search titles, people, or genres' })
      .fill('lighthouse');

    await expect(
      page.getByRole('heading', { level: 2, name: 'Results for “lighthouse”' })
    ).toBeVisible();
    // Matched on the synopsis, so the title itself never contains the query.
    const card = page.locator('a[href="/watch/wf-movie-harbourlights"]').first();
    await expect(card).toBeVisible();
    await expect(card).toContainText('Harbour Lights');
  });

  test('shows a no-results state instead of a blank grid', async ({ page }) => {
    await signedIn(page);
    await page.goto('/browse');
    await page.getByRole('button', { name: 'Search' }).click();
    await page
      .getByRole('textbox', { name: 'Search titles, people, or genres' })
      .fill('zzzznotatitle');

    await expect(
      page.getByRole('heading', { level: 2, name: 'Results for “zzzznotatitle”' })
    ).toBeVisible();
    await expect(
      page.getByRole('heading', { level: 3, name: 'No results found' })
    ).toBeVisible();
    await expect(page.getByText('Try searching for a different title.')).toBeVisible();
  });

  test('returns to the browse rows when the query is cleared', async ({ page }) => {
    await signedIn(page);
    await page.goto('/browse');
    await page.getByRole('button', { name: 'Search' }).click();
    const input = page.getByRole('textbox', { name: 'Search titles, people, or genres' });

    await input.fill('Lantern');
    await expect(page.locator(`a[href="/watch/wf-movie-lantern"]`)).toBeVisible();

    await input.fill('');
    await expect(
      page.getByRole('heading', { level: 2, name: 'Trending Now' })
    ).toBeVisible();
    await expect(
      page.getByRole('heading', { level: 2, name: 'Results for “Lantern”' })
    ).toHaveCount(0);
  });
});

test.describe('/browse — empty catalog', () => {
  test('renders no rows and no headings at all when the catalog is empty', async ({
    page,
  }) => {
    await signedIn(page, { content: [] });
    await page.goto('/browse');

    // HeroBanner returns null on an empty list and Row returns null on an
    // empty list, so the page ends up with no heading whatsoever. This test
    // pins that behaviour — and is the reason the missing h1 on /browse is
    // filed as an accessibility defect rather than a test gap.
    await expect(page.getByRole('heading')).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Home' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'My List' })).toBeVisible();
  });

  test('reports a gateway outage without crashing the shell', async ({ page }) => {
    await signedIn(page, { failAll: true });
    await page.goto('/browse');

    // react-query swallows the error; the navbar shell must survive and stay
    // navigable.
    await expect(page.getByRole('link', { name: 'My List' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Search' })).toBeVisible();
    await expect(page.getByRole('heading')).toHaveCount(0);
  });
});
