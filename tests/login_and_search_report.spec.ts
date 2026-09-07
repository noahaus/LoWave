import { test, expect } from '@playwright/test';

test('Access and Review NYC Client Report', async ({ page }) => {
  // Step 1: Go to the app home page
  await page.goto('http://localhost:3000', { waitUntil: 'networkidle' });
  // Step 2: Type demo@kestrel.app into the work email field
  await page.getByLabel('Work email', { exact: true }).fill('demo@kestrel.app');
  // Step 3: Type test1234 into the password field
  await page.getByLabel('Password', { exact: true }).fill('test1234');
  // Step 4: Click Sign in
  await page.getByTestId('login-submit').click();
  // Step 5: Assert the Dashboard heading is visible
  await expect(page.locator('b').getByText('Dashboard', { exact: true })).toContainText('Dashboard');
  // Step 6: Click Reports in the sidebar navigation
  await page.getByTestId('nav-reports').click();
  // Step 7: Type NYC into the Search reports field
  await page.getByTestId('reports-search').fill('NYC');
  // Step 8: Assert the reports table shows NYC client visit — Q2 review
  await expect(page.getByTestId('reports-rows')).toContainText('NYC client visit — Q2 review');
  // Step 9: Click the NYC client visit — Q2 review row
  await page.getByText('NYC client visit \\u2014 Q2 review', { exact: true }).click();
  // Step 10: Assert the report detail shows status Pending
  await expect(page.getByTestId('detail-status')).toContainText('Pending');
  // Step 11: Click Back to reports
  await page.getByText('Back to reports', { exact: true }).click();
});
