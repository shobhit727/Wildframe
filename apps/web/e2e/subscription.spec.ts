import { test, expect } from '@playwright/test';

test.describe('Subscription Purchase', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login');
    // Login first
    await page.fill('input[name="email"]', 'test@example.com');
    await page.fill('input[name="password"]', 'password123');
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/\/profile/);
  });

  test('should show subscription plans', async ({ page }) => {
    await page.goto('/subscription');
    await expect(page.locator('h1')).toContainText('Choose Your Plan');
    await expect(page.locator('[data-testid="plan-basic"]')).toBeVisible();
    await expect(page.locator('[data-testid="plan-premium"]')).toBeVisible();
    await expect(page.locator('[data-testid="plan-family"]')).toBeVisible();
  });

  test('should select a plan', async ({ page }) => {
    await page.goto('/subscription');
    await page.click('[data-testid="plan-premium"] button');
    await expect(page.locator('[data-testid="payment-form"]')).toBeVisible();
  });

  test('should process payment', async ({ page }) => {
    await page.goto('/subscription');
    await page.click('[data-testid="plan-premium"] button');
    
    // Fill payment form (test card)
    await page.fill('input[name="cardNumber"]', '4242424242424242');
    await page.fill('input[name="expiry"]', '12/30');
    await page.fill('input[name="cvc"]', '123');
    await page.fill('input[name="postalCode"]', '12345');
    
    await page.click('[data-testid="submit-payment"]');
    
    // Should show success
    await expect(page.locator('[data-testid="payment-success"]')).toBeVisible();
  });

  test('should show subscription in profile', async ({ page }) => {
    await page.goto('/profile');
    await expect(page.locator('[data-testid="active-subscription"]')).toBeVisible();
    await expect(page.locator('[data-testid="subscription-plan"]')).toContainText('Premium');
  });

  test('should allow subscription cancellation', async ({ page }) => {
    await page.goto('/profile');
    await page.click('[data-testid="manage-subscription"]');
    await page.click('[data-testid="cancel-subscription"]');
    await page.click('[data-testid="confirm-cancel"]');
    await expect(page.locator('[data-testid="subscription-cancelled"]')).toBeVisible();
  });
});
