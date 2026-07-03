const { test, expect } = require('./fixtures');
const { ADMIN_USER, ADMIN_PASSWORD } = require('./helpers');

test.describe('Admin login', () => {
  test('redirects to /admin/login when logged out', async ({ page, consoleErrors }) => {
    await page.goto('/admin');
    await expect(page).toHaveURL(/\/admin\/login$/);
    await expect(page.getByRole('heading', { name: /LotSpot Admin/i })).toBeVisible();
    expect(consoleErrors).toEqual([]);
  });

  test('wrong password shows the error alert and stays logged out', async ({ page, consoleErrors }) => {
    await page.goto('/admin/login');
    await page.getByLabel('Username').fill(ADMIN_USER);
    await page.getByLabel('Password').fill('definitely-wrong-password');
    await page.getByRole('button', { name: /sign in/i }).click();

    const alert = page.getByRole('alert');
    await expect(alert).toBeVisible();
    await expect(page).toHaveURL(/\/admin\/login$/);
    expect(consoleErrors).toEqual([]);
  });

  test('admin/admin logs in and Sign out returns to login', async ({ page, consoleErrors }) => {
    await page.goto('/admin/login');
    await page.getByLabel('Username').fill(ADMIN_USER);
    await page.getByLabel('Password').fill(ADMIN_PASSWORD);
    await page.getByRole('button', { name: /sign in/i }).click();

    await expect(page).toHaveURL(/\/admin\/inventory$/);
    await expect(page.getByRole('heading', { name: 'Inventory' })).toBeVisible();
    await expect(page.getByText(ADMIN_USER, { exact: true })).toBeVisible();

    await page.getByRole('button', { name: /sign out/i }).click();
    await expect(page).toHaveURL(/\/admin\/login$/);

    // Session is actually cleared, not just a route bounce.
    await page.goto('/admin');
    await expect(page).toHaveURL(/\/admin\/login$/);

    expect(consoleErrors).toEqual([]);
  });
});
