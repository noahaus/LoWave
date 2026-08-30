// playwright.config.ts
import { defineConfig } from '@playwright/test';

// Base URL of the app under test. Override per-run with QA_BASE_URL, e.g.
//   QA_BASE_URL=http://localhost:5173 npx playwright test
const baseURL = process.env.QA_BASE_URL || 'http://localhost:3000';

// Optional visual slow-motion for demos. Set QA_SLOWMO=0 to disable.
const slowMo = Number(process.env.QA_SLOWMO ?? 800);

export default defineConfig({
  testDir: './tests',
  use: {
    baseURL,
    launchOptions: { slowMo },
  },
});
