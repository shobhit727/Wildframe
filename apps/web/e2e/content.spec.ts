import { test, expect } from '@playwright/test';

test.describe('Content Playback', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('should show content library', async ({ page }) => {
    await page.goto('/browse');
    await expect(page.locator('h1')).toContainText('Browse');
  });

  test('should show content details', async ({ page }) => {
    await page.goto('/content/1');
    await expect(page.locator('[data-testid="content-title"]')).toBeVisible();
    await expect(page.locator('[data-testid="play-button"]')).toBeVisible();
  });

  test('should start playback', async ({ page }) => {
    await page.goto('/content/1');
    await page.click('[data-testid="play-button"]');
    await expect(page.locator('video')).toBeVisible();
    await expect(page.locator('video')).toHaveAttribute('src');
  });

  test('should show playback controls', async ({ page }) => {
    await page.goto('/content/1');
    await page.click('[data-testid="play-button"]');
    await expect(page.locator('[data-testid="play-pause"]')).toBeVisible();
    await expect(page.locator('[data-testid="progress-bar"]')).toBeVisible();
    await expect(page.locator('[data-testid="volume"]')).toBeVisible();
    await expect(page.locator('[data-testid="fullscreen"]')).toBeVisible();
  });

  test('should pause and resume playback', async ({ page }) => {
    await page.goto('/content/1');
    await page.click('[data-testid="play-button"]');
    await page.waitForTimeout(1000);
    await page.click('[data-testid="play-pause"]');
    const video = page.locator('video');
    await expect(video).toHaveJSProperty('paused', true);
    await page.click('[data-testid="play-pause"]');
    await expect(video).toHaveJSProperty('paused', false);
  });
});

test.describe('Content Search', () => {
  test('should search for content', async ({ page }) => {
    await page.goto('/search');
    await page.fill('input[name="q"]', 'test');
    await page.press('input[name="q"]', 'Enter');
    await expect(page.locator('[data-testid="search-results"]')).toBeVisible();
  });
});
