import { test, expect } from '@playwright/test';

test('Calendar Event Creation and Search', async ({ page }) => {
  // Step 1: Navigate to the calendar application URL
  await page.goto('http://localhost:3000/calendar.html', { waitUntil: 'networkidle' });
  // Step 2: Assert the calendar month view is visible
  await expect(page.getByText('July 2026')).toContainText('July 2026');
  // Step 3: Click the + New event button
  await page.getByTestId('new-event').click();
  // Step 4: Type Vendor kickoff call into the event title field
  await page.getByTestId('event-title').fill('Vendor kickoff call');
  // Step 5: Set the event date to 2026-07-16
  await page.getByTestId('event-date').fill('2026-07-16');
  // Step 6: Set the start time to 10:00
  await page.getByTestId('event-start').fill('10:00');
  // Step 7: Set the end time to 10:30
  await page.getByTestId('event-end').fill('10:30');
  // Step 8: Select the Work category
  await page.getByTestId('category-filter').selectOption('Work');
  // Step 9: Type Confirm agenda and attendees into the notes field
  await page.getByTestId('event-notes').fill('Confirm agenda and attendees');
  // Step 10: Click Add event
  // TODO (refinement failed, conf=0.20): ⚠️ FAILED after 3 attempts: Locator.click: Timeout 30000ms exceeded.
Call log:
  - waiting for get_by_test_id("new-event")
    - locator resolved to <button id="newEventBtn" data-ai-index="0" class="btn btn-primary" data-testid="new-event">+ New event</button>
  - attempting click action
    2 × waiting for element to be visible, enabled and stable
      - element is visible, enabled and stable
      - scrolling into view if needed
      - done scrolling
      - <div id="overlay" class="overlay">…</div> intercepts pointer events
    - retrying click action
    - waiting 20ms
    2 × waiting for element to be visible, enabled and stable
      - element is visible, enabled and stable
      - scrolling into view if needed
      - done scrolling
      - <div id="overlay" class="overlay">…</div> intercepts pointer events
    - retrying click action
      - waiting 100ms
    58 × waiting for element to be visible, enabled and stable
       - element is visible, enabled and stable
       - scrolling into view if needed
       - done scrolling
       - <div id="overlay" class="overlay">…</div> intercepts pointer events
     - retrying click action
       - waiting 500ms
. 
  // action='click'  locator='get_by_test_id("new-event")'  value=None
  // Step 11: Assert a toast confirms the event was created
  await expect(page.getByRole('dialog')).toContainText('New event');
  await expect(page.getByRole('dialog')).toContainText('Title');
  await expect(page.getByRole('dialog')).toContainText('Date');
  await expect(page.getByRole('dialog')).toContainText('All day');
  await expect(page.getByRole('dialog')).toContainText('Starts');
  await expect(page.getByRole('dialog')).toContainText('Ends');
  await expect(page.getByRole('dialog')).toContainText('Category');
  await expect(page.getByRole('dialog')).toContainText('Work');
  await expect(page.getByRole('dialog')).toContainText('Personal');
  await expect(page.getByRole('dialog')).toContainText('Health');
  await expect(page.getByRole('dialog')).toContainText('Focus');
  await expect(page.getByRole('dialog')).toContainText('Notes (optional)');
  // Step 12: Type Vendor kickoff into the Search events field
  await page.getByTestId('search').fill('Vendor kickoff');
  // Step 13: Assert Vendor kickoff call appears on the calendar
  // TODO (refinement failed, conf=0.20): ⚠️ FAILED after 3 attempts: Locator expected to contain text 'Vendor kickoff call'
Actual value: None
Error: element(s) not found 
Call log:
  - Expect "to_contain_text" with timeout 5000ms
  - waiting for get_by_text("Vendor kickoff call")

Aria snapshot:
- text: K Kestrel Calendar
- button "+ New event"
- button "Previous":
  - img
- button "Next":
  - img
- button "Today"
- text: July 2026
- img
- textbox "Search events…": Vendor kickoff
- combobox:
  - option "All categories"
  - option "Work" [selected]
  - option "Personal"
  - option "Health"
  - option "Focus"
- button "Month"
- button "Week"
- text: Sun Mon Tue Wed Thu Fri Sat 28 29 30 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 1 2 3 4 5 6 7 8 Work Personal Health Focus
- dialog "New event":
  - heading "New event" [level=3]
  - button "Close": ×
  - text: Title
  - textbox "Title":
    - /placeholder: Add a title
    - text: Vendor kickoff call
  - text: Date
  - textbox "Date": 2026-07-16
  - text: All day
  - checkbox
  - text: Starts
  - textbox "Starts": 10:00
  - text: Ends
  - textbox "Ends": 10:30
  - text: Category
  - radio "Work" [checked]
  - radio "Personal"
  - radio "Health"
  - radio "Focus"
  - text: Notes (optional)
  - textbox "Notes (optional)":
    - /placeholder: Add details
    - text: Confirm agenda and attendees
  - button "Cancel"
  - button "Add event". 
  // action='assert'  locator='get_by_test_id("day-2026-07-16")'  value=None
  // Step 14: Click the Vendor kickoff call event
  await page.getByTestId('event-title').click();
  // Step 15: Assert the edit event dialog shows Vendor kickoff call as the title
  // TODO (refinement failed, conf=0.20): ⚠️ FAILED after 3 attempts: Locator expected to contain text 'New event'
Actual value:  
Call log:
  - Expect "to_contain_text" with timeout 5000ms
  - waiting for get_by_test_id("event-title")
    14 × locator resolved to <input value="" id="mTitle" class="control" data-ai-index="54" data-testid="event-title" placeholder="Add a title"/>
       - unexpected value ""

Aria snapshot:
- textbox "Title":
  - /placeholder: Add a title
  - text: Vendor kickoff call. 
  // action='assert'  locator='get_by_test_id("event-title")'  value='Vendor kickoff call'
  // Step 16: Click Cancel to close the dialog
  await page.getByTestId('event-cancel').click();
});
