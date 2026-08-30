import { test, expect } from '@playwright/test';

test('Test 1', async ({ page }) => {
  // Step 1: Navigates to the Kestrel application at base_url.
  await page.goto('http://localhost:3000', { waitUntil: 'networkidle' });
  // Step 2: Types 'demo@kestrel.app' into the work email field and 'test1234' into the password field.
  await page.getByTestId('login-email').fill('demo@kestrel.app');
  // Step 3: Types 'test1234' into the password field.
  await page.getByTestId('login-password').fill('test1234');
  // Step 4: Clicks the sign in button to submit login credentials.
  await page.getByTestId('login-submit').click();
  // Step 5: Asserts that the report details page is displayed with a pending status.
  // TODO (refinement failed, conf=0.20): ⚠️ FAILED after 3 attempts: Locator.wait_for: Timeout 5000ms exceeded.
Call log:
  - waiting for get_by_text("Asserts that the report details page is displayed with a pending status.").first to be visible
. 
  // action='assert'  locator='get_by_role("link")'  value=None
  // Step 6: Navigates back to the reports page.
  await page.getByTestId('nav-reports').click();
});
