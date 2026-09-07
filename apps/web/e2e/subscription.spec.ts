import { test, expect } from '@playwright/test';

test.describe('Subscription Page', () => {
  test('should show login page when accessing subscription', async ({ page }) => {
    await page.goto('/subscription');
    // /subscription requires auth - should redirect to login
    await expect(page).toHaveURL(/\/login/);
  });
});
