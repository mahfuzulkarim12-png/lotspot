const base = require('@playwright/test');

// Chromium logs a "Failed to load resource: the server responded with a
// status of NNN" console entry for every non-2xx fetch/XHR response — this is
// the browser's own network diagnostic, not a JS error the app raised. Several
// flows in this app intentionally exercise 401/404/409 responses (wrong
// password, overselling, bad POS key), so that line is expected noise, not a
// real bug. Filter it out to keep the assertion meaningful for actual JS
// errors/exceptions/React warnings.
const NETWORK_STATUS_NOISE = /^Failed to load resource: the server responded with a status of \d+/;

// Firefox logs its own diagnostic when a long-lived EventSource (the SSE
// inventory stream at /api/events) gets torn down by page navigation — this
// is Firefox's network layer reporting the deliberate disconnect, not an app
// error. Chromium and WebKit don't surface this as a console error.
const SSE_NAVIGATION_NOISE = /The connection to .*\/api\/events was interrupted while the page was loading/;

// Extends the base test with automatic console-error capture so every spec
// can assert `expect(consoleErrors).toEqual([])` at the end of its flow.
const test = base.test.extend({
  consoleErrors: async ({ page }, use) => {
    const errors = [];
    page.on('console', (msg) => {
      const text = msg.text();
      if (msg.type() === 'error' && !NETWORK_STATUS_NOISE.test(text) && !SSE_NAVIGATION_NOISE.test(text)) {
        errors.push(text);
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
