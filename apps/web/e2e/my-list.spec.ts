/**
 * My List — `/my-list`.
 *
 * Two independent sources: "Continue Watching" is built from live playback
 * sessions, "My List" is a localStorage watchlist resolved through the
 * content service.
 */
import {
  test,
  expect,
  anonymous,
  signedIn,
  expectRedirectedToLogin,
  VIEWER,
  MOVIE_ID,
  SHOW_ID,
  WATCH_HISTORY_SESSIONS,
} from './fixtures';

const LIST_KEY = `wildframe_my_list:${VIEWER.id}`;

test.describe('/my-list — gate', () => {
  test('sends an anonymous visitor to the sign-in form', async ({ page }) => {
    await anonymous(page);
    await page.goto('/my-list');
    await expectRedirectedToLogin(page);
  });
});

test.describe('/my-list — continue watching', () => {
  test('renders the two tabs with their counts', async ({ page }) => {
    await signedIn(page);
    await page.goto('/my-list');

    await expect(page.getByRole('heading', { level: 1, name: 'My List' })).toBeVisible();
    await expect(page.getByRole('button', { name: /Continue Watching/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /My List/ })).toBeVisible();
  });

  test('shows the empty state when nothing is in progress', async ({ page }) => {
    await signedIn(page);
    await page.goto('/my-list');

    await expect(
      page.getByRole('heading', { level: 3, name: 'Nothing in progress' })
    ).toBeVisible();
    await expect(
      page.getByText('Start watching a title and it will show up here.')
    ).toBeVisible();
  });

  test('resumes an in-flight session and hides ended ones', async ({ page }) => {
    await signedIn(page, { watchHistory: WATCH_HISTORY_SESSIONS });
    await page.goto('/my-list');

    await expect(page.locator(`a[href="/watch/${MOVIE_ID}"]`)).toHaveCount(1);
    await expect(
      page.getByText('Resume playback and your position saves automatically.')
    ).toBeVisible();
    // The Meridian session in the fixture is `ended`, so the client filters it.
    await expect(page.locator(`a[href="/watch/${SHOW_ID}"]`)).toHaveCount(0);
    await expect(
      page.getByRole('heading', { level: 3, name: 'Nothing in progress' })
    ).toHaveCount(0);
  });
});

test.describe('/my-list — watchlist tab', () => {
  test('shows the empty state when the local watchlist has no ids', async ({ page }) => {
    await signedIn(page);
    await page.goto('/my-list');
    await page.getByRole('button', { name: /My List/ }).click();

    await expect(
      page.getByRole('heading', { level: 3, name: 'Your list is empty' })
    ).toBeVisible();
    await expect(
      page.getByText('Tap the My List button on any title to save it here.')
    ).toBeVisible();
  });

  test('resolves locally stored ids into titles', async ({ page }) => {
    await signedIn(page);
    // The watchlist lives in localStorage, so it must be seeded before the
    // page's first render reads it.
    await page.addInitScript(
      ([key, ids]) => window.localStorage.setItem(key as string, JSON.stringify(ids)),
      [LIST_KEY, [MOVIE_ID, 'wf-movie-lantern']] as const
    );

    await page.goto('/my-list');
    await page.getByRole('button', { name: /My List/ }).click();

    await expect(page.locator(`a[href="/watch/${MOVIE_ID}"]`)).toHaveCount(1);
    await expect(page.locator('a[href="/watch/wf-movie-lantern"]')).toHaveCount(1);
    await expect(
      page.getByRole('heading', { level: 3, name: 'Your list is empty' })
    ).toHaveCount(0);
  });

  test('drops an id from the watchlist when Remove is pressed', async ({ page }) => {
    await signedIn(page);
    await page.addInitScript(
      ([key, ids]) => window.localStorage.setItem(key as string, JSON.stringify(ids)),
      [LIST_KEY, [MOVIE_ID, 'wf-movie-lantern']] as const
    );

    await page.goto('/my-list');
    await page.getByRole('button', { name: /My List/ }).click();
    await expect(page.locator(`a[href="/watch/${MOVIE_ID}"]`)).toHaveCount(1);

    await page
      .getByRole('button', { name: 'Remove Nightfall Protocol from My List' })
      .click();

    await expect(page.locator(`a[href="/watch/${MOVIE_ID}"]`)).toHaveCount(0);
    await expect(page.locator('a[href="/watch/wf-movie-lantern"]')).toHaveCount(1);

    const stored = await page.evaluate(
      (key) => JSON.parse(window.localStorage.getItem(key) ?? '[]'),
      LIST_KEY
    );
    expect(stored).toEqual(['wf-movie-lantern']);
  });

  test('does not leak one user’s watchlist into another account', async ({ page }) => {
    await signedIn(page, { user: { ...VIEWER, id: 'e2e-user-other' } });
    await page.addInitScript(
      ([key, ids]) => window.localStorage.setItem(key as string, JSON.stringify(ids)),
      [LIST_KEY, [MOVIE_ID]] as const
    );

    await page.goto('/my-list');
    await page.getByRole('button', { name: /My List/ }).click();

    // The list is keyed by user id, so a different account starts empty.
    await expect(
      page.getByRole('heading', { level: 3, name: 'Your list is empty' })
    ).toBeVisible();
  });
});
