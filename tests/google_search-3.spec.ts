import { test, expect } from '@playwright/test';

test('Google Search and Navigation', async ({ page }) => {
  // Step 1: Open Google homepage
  await page.goto('https://www.google.com/', { waitUntil: 'networkidle' });
  // Step 2: Type 'Playwright testing tutorial' into the search box
  await page.getByRole('combobox', { name: 'Search', exact: true }).fill('Playwright testing tutorial');
  // Step 3: Press Enter to search
  await page.getByRole('combobox', { name: 'Search', exact: true }).focus();
  await page.keyboard.press('Enter');
  // Step 4: Click on the 'Images' tab
  await page.getByRole('link', { name: 'Images', exact: true }).click();
  // Step 5: Click on the 'All' tab
  // TODO (refinement failed, conf=0.10): ⚠️ FAILED after 1 attempts: Page.wait_for_timeout: Target page, context or browser has been closed
  // action='click'  locator='locator("n/a")'  value='All'
});
