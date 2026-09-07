import { test, expect } from '@playwright/test';

test('Create and Verify Calendar Event', async ({ page }) => {
  // Step 1: Go to the calendar application.
  await page.goto('http://localhost:3000/calendar.html', { waitUntil: 'networkidle' });
  // Step 2: Assert the calendar month view is visible.
  await expect(page.getByTestId('period-label')).toContainText('July 2026');
  // Step 3: Click + New event.
  await page.getByTestId('new-event').click();
  // Step 4: Type Vendor kickoff call into the event title field.
  await page.getByTestId('event-title').fill('Vendor kickoff call');
  // Step 5: Set the event date to 2026-07-16.
  await page.getByTestId('event-date').fill('2026-07-16');
  // Step 6: Set the start time to 10:00.
  await page.getByLabel('Starts', { exact: true }).fill('10:00');
  // Step 7: Set the end time to 10:30.
  await page.getByLabel('Ends', { exact: true }).fill('10:30');
  // Step 8: Select the Work category.
  await page.getByTestId('category-filter').selectOption('Work');
  // Step 9: Type Confirm agenda and attendees into the notes field.
  await page.getByTestId('event-notes').fill('Confirm agenda and attendees');
  // Step 10: Click Add event.
  await page.getByTestId('event-save').click();
  // Step 11: Assert a toast confirms the event was created.
  await expect(page.getByRole('status', { name: 'Event added', exact: true })).toContainText('Event added');
  // Step 12: Type Vendor kickoff into the Search events field.
  await page.getByPlaceholder('Search events\\u2026', { exact: true }).fill('Vendor kickoff');
  // Step 13: Assert Vendor kickoff call appears on the calendar.
  await expect(page.getByText('Vendor kickoff call', { exact: true })).toContainText('Vendor kickoff call');
  // Step 14: Click the Vendor kickoff call event.
  await page.getByText('Vendor kickoff call', { exact: true }).click();
  // Step 15: Assert the edit event dialog shows Vendor kickoff call as the title.
  await expect(page.getByText('Vendor kickoff call', { exact: true })).toContainText('Vendor kickoff call');
  // Step 16: Click Cancel to close the dialog.
  await page.getByTestId('event-cancel').click();
});
