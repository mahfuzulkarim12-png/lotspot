const base = require('@playwright/test');

// Chromium logs a "Failed to load resource: the server responded with a
// status of NNN" console entry for every non-2xx fetch/XHR response — this is
// the browser's own network diagnostic, not a JS error the app raised. Several
// flows in this app intentionally exercise 401/404/409 responses (wrong
// password, overselling, bad POS key), so that line is expected noise, not a
// real bug. Filter it out to keep the assertion meaningful for actual JS
// errors/exceptions/React warnings.
const NETWORK_STATUS_NOISE = /^Failed to load resource: the server responded with a status of \d+/;

// Extends the base test with automatic console-error capture so every spec
// can assert `expect(consoleErrors).toEqual([])` at the end of its flow.
const test = base.test.extend({
  consoleErrors: async ({ page }, use) => {
    const errors = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error' && !NETWORK_STATUS_NOISE.test(msg.text())) {
        errors.push(msg.text());
      }
    });
    page.on('pageerror', (err) => {
      errors.push(String(err));
    });
    await use(errors);
  },
});

const expect = base.expect;

module.exports = { test, expect };
