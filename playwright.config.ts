// playwright.config.ts
import { defineConfig } from '@playwright/test';
import fs from 'fs';

// Base URL of the app under test. Override per-run with QA_BASE_URL, e.g.
//   QA_BASE_URL=http://localhost:5173 npx playwright test
const baseURL = process.env.QA_BASE_URL || 'http://localhost:3000';

// GUI Test with "With browser" sets QA_HEADED=1 so Chromium stays visible.
const headed = process.env.QA_HEADED === '1' || process.env.QA_HEADED === 'true';

// Optional visual slow-motion for demos. Set QA_SLOWMO=0 to disable.
const slowMo = Number(process.env.QA_SLOWMO ?? (headed ? 800 : 0));

// Generated specs live here; create the dir so the generated project can load
// even before the first pipeline run.
fs.mkdirSync('./outputs/tests', { recursive: true });

export default defineConfig({
  testDir: './tests',
  outputDir: './outputs/playwright/test-results',
  reporter: [
    ['list'],
    ['html', { outputFolder: './outputs/playwright/report', open: 'never' }],
  ],
  use: {
    baseURL,
    // launchOptions.headless would override CLI --headed; only force a window when QA_HEADED is set.
    ...(headed ? { headless: false } : {}),
    launchOptions: { slowMo },
  },
  projects: [
    { name: 'tests', testDir: './tests', testIgnore: '**/generated/**' },
    { name: 'generated', testDir: './outputs/tests' },
  ],
});
