// Deterministic real-browser checks. No model calls, network or target-app writes.
const { createRequire } = require('node:module');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const repo = process.argv[2] || path.resolve(__dirname, '..');
const localRequire = createRequire(path.join(repo, 'package.json'));
const { chromium, expect } = localRequire('@playwright/test');
const cases = JSON.parse(execFileSync(path.join(repo, '.venv/bin/python'), [path.join(__dirname, 'verify_generated.py'), repo], { encoding: 'utf8' }));
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  let failures = 0;
  try {
    for (const item of cases) {
      for (const [version, generated] of Object.entries(item.versions)) {
        const outcomes = {};
        for (const kind of ['good', 'bad']) {
          const page = await browser.newPage();
          page.setDefaultTimeout(700);
          try {
            await page.setContent(item[kind]);
            if (generated.error) throw new Error(generated.error);
            const run = new (Object.getPrototypeOf(async function () {}).constructor)('page', 'expect', generated.code);
            await run(page, expect.configure({ timeout: 700 }));
            outcomes[kind] = 'passed';
          } catch (error) {
            outcomes[kind] = 'failed';
            outcomes[kind + 'Reason'] = String(error.message).split('\n')[0];
            outcomes[kind + 'AssertionFailure'] = /expect\(locator\)\.(toHaveValue|toBeChecked|toContainText)\((?:expected)?\) failed/.test(String(error.message));
          } finally { await page.close(); }
        }
        console.log(JSON.stringify({ case: item.name, version, ...outcomes }));
        if (version === 'contribution' && (outcomes.good !== 'passed' || outcomes.bad !== 'failed' || !outcomes.badAssertionFailure)) failures++;
      }
    }
  } finally { await browser.close(); }
  process.exitCode = failures ? 1 : 0;
})().catch(error => { console.error(error.message); process.exitCode = 1; });
