import { test, expect } from '@playwright/test';

test('Report Navigation and Validation Workflow', async ({ page }) => {
  // Step 1: Navigate to the app home page
  await page.goto('http://localhost:3000', { waitUntil: 'networkidle' });
  // Step 2: Type demo@kestrel.app into the work email field
  await page.getByLabel('Work email').fill('demo@kestrel.app');
  // Step 3: Type test1234 into the password field
  await page.getByTestId('login-password').fill('test1234');
  // Step 4: Click Sign in
  await page.getByTestId('login-submit').click();
  // Step 5: Assert the Dashboard heading is visible
  await expect(page.getByTestId('nav-dashboard')).toContainText('Dashboard');
  // Step 6: Click Reports in the sidebar navigation
  await page.getByTestId('nav-reports').click();
  // Step 7: Type NYC into the Search reports field
  await page.getByTestId('reports-search').fill('NYC');
  // Step 8: Assert the reports table shows NYC client visit — Q2 review
  await expect(page.getByTestId('reports-rows')).toContainText('NYC client visit — Q2 review');
  await expect(page.getByTestId('reports-rows')).toContainText('EXP-1042');
  await expect(page.getByTestId('reports-rows')).toContainText('Travel');
  await expect(page.getByTestId('reports-rows')).toContainText('2026-06-28');
  await expect(page.getByTestId('reports-rows')).toContainText('Pending');
  await expect(page.getByTestId('reports-rows')).toContainText('$1,284.50');
  // Step 9: Click the NYC client visit — Q2 review row
  await page.getByTestId('reports-rows').click();
  // Step 10: Assert the report detail shows status Pending
  await expect(page.getByTestId('detail-status')).toContainText('Pending');
  // Step 11: Click Back to reports
  await page.getByText('Back to reports').click();
});
