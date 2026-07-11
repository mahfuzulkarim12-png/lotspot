const { test, expect } = require('./fixtures');
const { ADMIN_USER, ADMIN_PASSWORD, uniqueSuffix, apiLogin, apiRequest } = require('./helpers');

async function login(page) {
  await page.goto('/admin/login');
  await page.getByLabel('Username').fill(ADMIN_USER);
  await page.getByLabel('Password').fill(ADMIN_PASSWORD);
  await page.getByRole('button', { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/admin\/inventory$/);
}

test.describe('Time clock', () => {
  let token;

  test.beforeAll(async () => {
    token = await apiLogin();
  });

  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('add employee, clock in live via SSE, reject a double clock-in, clock out, see shift history', async ({
    page,
    browser,
    consoleErrors,
  }) => {
    const name = `E2E Timeclock ${uniqueSuffix()}`;
    const pin = '1357';

    await page.goto('/admin/timeclock');
    await expect(page.getByRole('heading', { name: 'Time clock' })).toBeVisible();

    // Add employee via the on-page form.
    await page.getByLabel('Name').fill(name);
    await page.getByLabel('New PIN').fill(pin);
    await page.getByRole('button', { name: 'Add employee' }).click();
    await expect(page.getByLabel('Employee').locator('option', { hasText: name })).toHaveCount(1);

    // A second admin tab, deliberately kept "stale": its SSE stream is
    // blocked so it never learns the employee got clocked in on the primary
    // tab. This reproduces a genuine double clock-in deterministically
    // (two admins racing to clock the same person in) without depending on
    // real click-timing, which is the kind of SSE timing flakiness that bit
    // this suite before (see commit 57a46e8).
    const staleContext = await browser.newContext();
    const stalePage = await staleContext.newPage();
    await stalePage.route('**/api/events', (route) => route.abort());
    await login(stalePage);
    await stalePage.goto('/admin/timeclock');
    await stalePage.getByLabel('Employee').selectOption({ label: name });
    await expect(stalePage.getByRole('button', { name: 'Clock in' })).toBeVisible();

    try {
      // Clock in on the primary tab — the "currently clocked in" table must
      // update without a page reload (proves the SSE wiring, not just the
      // REST call).
      await page.getByLabel('Employee').selectOption({ label: name });
      await page.getByLabel('PIN', { exact: true }).fill(pin);
      await page.getByRole('button', { name: 'Clock in' }).click();

      const openShiftRow = page.locator('tbody tr', { hasText: name });
      await expect(openShiftRow).toBeVisible({ timeout: 2000 });
      await expect(page.getByRole('status')).toContainText('clocked in at');
      await expect(page.getByRole('button', { name: 'Clock out' })).toBeVisible();

      // The stale tab still believes this employee isn't clocked in and
      // submits its own "Clock in" — the backend must reject it with 409,
      // surfaced as a visible alert.
      await stalePage.getByLabel('PIN', { exact: true }).fill(pin);
      await stalePage.getByRole('button', { name: 'Clock in' }).click();
      const staleAlert = stalePage.getByRole('alert');
      await expect(staleAlert).toBeVisible();
      await expect(staleAlert).toContainText(`${name} already has an open shift`);

      // The rejected double clock-in must not have created a second open
      // shift for this employee.
      await expect(page.locator('tbody tr', { hasText: name })).toHaveCount(1);

      // Clock out — row disappears live, no reload. The PIN field clears
      // after every successful submit, so it must be re-entered here.
      await page.getByLabel('PIN', { exact: true }).fill(pin);
      await page.getByRole('button', { name: 'Clock out' }).click();
      await expect(page.locator('tbody tr', { hasText: name })).toHaveCount(0, { timeout: 2000 });
      await expect(page.getByRole('status')).toContainText('clocked out at');
    } finally {
      await staleContext.close();
    }

    // Shift history shows a real, computed duration for the completed shift.
    await page.goto('/admin/timeclock-history');
    await expect(page.getByRole('heading', { name: 'Shift history' })).toBeVisible();
    const historyRow = page.locator('tbody tr', { hasText: name });
    await expect(historyRow).toBeVisible();
    await expect(historyRow).not.toContainText('—');
    await expect(historyRow).not.toContainText('In progress');

    expect(consoleErrors).toEqual([]);
  });

  test('clock-action form blocks submit client-side with no employee selected or an empty PIN', async ({
    page,
    consoleErrors,
  }) => {
    const name = `E2E Timeclock Validation ${uniqueSuffix()}`;
    const { envelope } = await apiRequest('/api/employees', {
      method: 'POST',
      token,
      body: { name, pin: '2468' },
    });
    if (!envelope.success) throw new Error(`create employee failed: ${envelope.error}`);

    let clockRequestCount = 0;
    await page.route('**/api/timeclock/clock-*', (route) => {
      clockRequestCount += 1;
      route.continue();
    });

    await page.goto('/admin/timeclock');
    await expect(page.getByLabel('Employee').locator('option', { hasText: name })).toHaveCount(1);

    // No employee selected.
    await page.getByRole('button', { name: 'Clock in' }).click();
    await expect(page.getByRole('alert')).toHaveText('Select an employee.');

    // Employee selected, PIN left empty.
    await page.getByLabel('Employee').selectOption({ label: name });
    await page.getByRole('button', { name: 'Clock in' }).click();
    await expect(page.getByRole('alert')).toHaveText('Enter a PIN.');

    expect(clockRequestCount).toBe(0);
    expect(consoleErrors).toEqual([]);
  });
});
