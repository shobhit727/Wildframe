/**
 * Public landing page — `/`.
 *
 * Regression guard for the marketing surface: hero copy, feature grid, FAQ
 * disclosure behaviour, and the sign-in/sign-up entry points.
 */
import { test, expect } from './fixtures';

test.describe('Home page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('renders the hero with its exact headline and calls to action', async ({ page }) => {
    await expect(page).toHaveTitle(/Wildframe/i);
    await expect(
      page.getByRole('heading', { level: 1, name: 'Stories that pull you in.' })
    ).toBeVisible();
    await expect(page.getByText('STREAM SOMETHING GREAT')).toBeVisible();
    await expect(
      page.getByText('Explore movies and series through a dark, cinematic interface')
    ).toBeVisible();
    await expect(page.getByRole('link', { name: /Start exploring/ })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Browse titles' })).toBeVisible();
  });

  test('lists the three feature cards in order', async ({ page }) => {
    await expect(
      page.getByRole('heading', {
        level: 2,
        name: 'A cleaner way to find your next watch.',
      })
    ).toBeVisible();

    const features = page.locator('section', {
      has: page.getByText('BUILT FOR WATCHING'),
    }).getByRole('heading', { level: 3 });
    await expect(features).toHaveText([
      'Your library, wherever you are.',
      'Find something worth watching.',
      'Keep the good stuff close.',
    ]);
  });

  test('expands an FAQ answer only when its question is opened', async ({ page }) => {
    // The <summary> also holds a decorative "+" glyph, so target the element
    // rather than an exact-text match.
    const faq = page.locator('details', { has: page.locator('summary', { hasText: 'What is Wildframe?' }) });
    const summary = faq.locator('summary');
    const answer = page.getByText(/Wildframe is a streaming platform project/);

    await expect(summary).toBeVisible();
    await expect(answer).not.toBeVisible();

    await summary.click();
    await expect(answer).toBeVisible();
  });

  test('routes the sign-in and sign-up entry points to the right pages', async ({ page }) => {
    await page.getByRole('link', { name: 'Sign In' }).first().click();
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Sign In' })).toBeVisible();

    await page.getByRole('link', { name: 'Sign up now' }).click();
    await expect(page).toHaveURL(/\/signup$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Create Account' })).toBeVisible();
  });

  test('closes with a create-account call to action and a current-year footer', async ({
    page,
  }) => {
    await expect(
      page.getByRole('heading', { level: 2, name: 'Ready to explore?' })
    ).toBeVisible();

    // "Create account" appears in the closing CTA and again in the footer.
    const cta = page.getByRole('link', { name: 'Create account', exact: true }).first();
    await expect(cta).toHaveAttribute('href', '/signup');
    await expect(
      page.getByRole('link', { name: 'Create account', exact: true })
    ).toHaveCount(2);

    await expect(
      page.getByText(`© ${new Date().getFullYear()} Wildframe.`)
    ).toBeVisible();
  });
});
