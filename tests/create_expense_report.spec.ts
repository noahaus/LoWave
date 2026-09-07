import { test, expect } from '@playwright/test';

test('Create and Submit a New Report', async ({ page }) => {
  // Step 1: Go to the app home page
  await page.goto('http://localhost:3000', { waitUntil: 'networkidle' });
  // Step 2: Type demo@kestrel.app into the work email field
  await page.getByLabel('Work email', { exact: true }).fill('demo@kestrel.app');
  // Step 3: Type test1234 into the password field
  await page.getByLabel('Password', { exact: true }).fill('test1234');
  // Step 4: Click Sign in
  await page.getByTestId('login-submit').click();
  // Step 5: Click New report on the dashboard
  await page.getByTestId('dash-new-report').click();
  // Step 6: Type Seattle customer workshop into the report title field
  await page.getByLabel('Report title', { exact: true }).fill('Seattle customer workshop');
  // Step 7: Set the trip date to 2026-07-10
  await page.getByLabel('Trip date', { exact: true }).fill('2026-07-10');
  // Step 8: Select Travel from the category dropdown
  await page.getByLabel('Primary category', { exact: true }).selectOption('Travel');
  // Step 9: Type On-site workshop with Acme into the notes field
  await page.getByLabel('Notes (optional)", exact=True).fill('On-site workshop with Acme');
  // Step 10: Click Continue
  await page.getByTestId('wiz-next').click();
  // Step 11: Type Airport rideshare into the first line item description
  await page.getByTestId('li-desc-0').fill('Airport rideshare');
  // Step 12: Type 48.50 into the first line item amount
  await page.getByTestId('li-amt-0').fill('48.50');
  // Step 13: Click Add line item
  await page.getByTestId('wiz-add-item').click();
  // Step 14: Type Hotel — 1 night into the second line item description
  await page.getByTestId('li-desc-1').fill('Hotel — 1 night');
  // Step 15: Type 189.00 into the second line item amount
  await page.getByTestId('li-amt-1').fill('189.00');
  // Step 16: Assert the wizard total reflects both line items
  await expect(page.getByTestId('wiz-total')).toContainText('$237.50');
  // Step 17: Click Review
  await page.getByTestId('wiz-next').click();
  // Step 18: Assert the review step shows Seattle customer workshop
  await expect(page.locator('b').getByText('Seattle customer workshop', { exact: true })).toContainText('Seattle customer workshop');
  // Step 19: Click Submit for approval
  await page.getByTestId('wiz-submit').click();
  // Step 20: Assert you land back on the Reports list
  await expect(page.getByText('Reports', { exact: true })).toContainText('Reports');
  // Step 21: Assert a report titled Seattle customer workshop is visible
  await expect(page.locator('b').getByText('Seattle customer workshop', { exact: true })).toContainText('Seattle customer workshop');
});
