/**
 * Playback detail surface — `/watch/[id]`.
 *
 * This is the route the old `content.spec.ts` meant to cover when it visited
 * `/content/1` (which does not exist).
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

test.describe('/watch/[id] — gate', () => {
  test('sends an anonymous visitor to the sign-in form', async ({ page }) => {
    await anonymous(page);
    await page.goto(`/watch/${MOVIE_ID}`);
    await expectRedirectedToLogin(page);
  });
});

test.describe('/watch/[id] — movie', () => {
  test.beforeEach(async ({ page }) => {
    await signedIn(page);
    await page.goto(`/watch/${MOVIE_ID}`);
  });

  test('shows the title, match score, rating, runtime and certificate', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 1, name: 'Nightfall Protocol' })
    ).toBeVisible();
    // The card grid also renders "N% Match" in its hover overlay, hence .first().
    await expect(page.getByText('84% Match').first()).toBeVisible();
    await expect(page.getByText('★ 8.4', { exact: true })).toBeVisible();
    await expect(page.getByText('2024', { exact: true })).toBeVisible();
    await expect(page.getByText('1h 58m', { exact: true })).toBeVisible();
    await expect(page.getByText('PG-13', { exact: true })).toBeVisible();
    await expect(page.getByText('HD', { exact: true })).toBeVisible();
  });

  test('shows the synopsis and joined genres', async ({ page }) => {
    await expect(
      page.getByText('A courier races a dying city to hand off a single reel of film.')
    ).toBeVisible();
    // "Genres:" is a <span> sibling of the joined genre text, so the
    // assertion targets the combined line rather than the value alone.
    await expect(page.getByText('Genres: Thriller')).toBeVisible();
  });

  test('falls back to the preview stream when the title has no packaged media', async ({
    page,
  }) => {
    await expect(
      page.getByText('Preview stream — no packaged media for this title yet.')
    ).toBeVisible();
  });

  test('recommends similar titles but never the one being watched', async ({ page }) => {
    await expect(
      page.getByRole('heading', { level: 2, name: 'More Like This' })
    ).toBeVisible();

    // Similar content is fetched with page_size 12 and excludes the current
    // id, so the watched title must not appear in its own carousel.
    await expect(page.locator(`a[href="/watch/${MOVIE_ID}"]`)).toHaveCount(0);
    await expect(page.locator(`a[href="/watch/wf-movie-lantern"]`)).toHaveCount(1);
    await expect(page.locator(`a[href="/watch/${SHOW_ID}"]`)).toHaveCount(1);
  });

  test('navigates back to browse from both trailing links', async ({ page }) => {
    const backLinks = page.getByRole('link', { name: 'Back to Browse' });
    await expect(backLinks).toHaveCount(2);

    await backLinks.last().click();
    await expect(page).toHaveURL(/\/browse$/);
    await expect(
      page.getByRole('heading', { level: 2, name: 'Trending Now' })
    ).toBeVisible();
  });

  test('toggles the title in and out of My List', async ({ page }) => {
    const toggle = page.getByRole('button', { name: 'Toggle my list' });
    await expect(toggle).toBeVisible();

    await toggle.click();
    await expect(page.getByText('Added to My List')).toBeVisible();

    await toggle.click();
    await expect(page.getByText('Removed from My List')).toBeVisible();
  });
});

test.describe('/watch/[id] — series', () => {
  test('lists the season and its episodes', async ({ page }) => {
    await signedIn(page);
    await page.goto(`/watch/${SHOW_ID}`);

    await expect(
      page.getByRole('heading', { level: 1, name: 'Meridian Station' })
    ).toBeVisible();
    await expect(
      page.getByRole('heading', { level: 2, name: 'Season 1 · 2 episodes' })
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: /Episode 1: Departure Board/ })
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: /Episode 2: Sleeper Car/ })
    ).toBeVisible();
  });

  test('appends the episode number to the title once one is selected', async ({ page }) => {
    await signedIn(page);
    await page.goto(`/watch/${SHOW_ID}`);

    await page.getByRole('button', { name: /Episode 2: Sleeper Car/ }).click();

    await expect(
      page.getByRole('heading', { level: 1, name: /Meridian Station · Ep 2/ })
    ).toBeVisible();
  });
});

test.describe('/watch/[id] — unknown id', () => {
  test('renders the player shell without inventing content', async ({ page }) => {
    await signedIn(page);
    await page.goto('/watch/wf-movie-does-not-exist');

    // No title block, so no h1 and no "More Like This" carousel — the page
    // must not fall back to some other title's data.
    await expect(page.getByRole('heading', { level: 1 })).toHaveCount(0);
    await expect(
      page.getByRole('heading', { level: 2, name: 'More Like This' })
    ).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Back to Browse' }).first()).toBeVisible();
  });
});
