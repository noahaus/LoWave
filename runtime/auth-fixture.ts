import { test as base, expect } from '@playwright/test';
const { runAuthHook } = require('./auth-hook-runner.cjs');

const test = base.extend({
  storageState: async ({ baseURL }, use) => {
    const hookPath = process.env.QA_AUTH_HOOK;
    if (!hookPath) throw new Error('Runtime authentication was selected but QA_AUTH_HOOK is missing');
    if (!baseURL) throw new Error('Runtime authentication requires a base URL');
    const state = await runAuthHook({ hookPath, baseURL });
    await use(state);
  },
});

export { test, expect };
