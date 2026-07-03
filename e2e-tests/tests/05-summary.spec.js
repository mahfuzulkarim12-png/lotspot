const { test, expect } = require('./fixtures');
const {
  ADMIN_USER,
  ADMIN_PASSWORD,
  uniqueSuffix,
  apiLogin,
  apiCreateProduct,
  apiDeleteProduct,
  apiSalesSummary,
  todayISO,
  formatCents,
  selectProductByName,
} = require('./helpers');

test.describe('Daily summary', () => {
  let token;
  const createdIds = [];

  test.beforeAll(async () => {
    token = await apiLogin();
  });

  test.afterEach(async () => {
    while (createdIds.length) {
      const id = createdIds.pop();
      await apiDeleteProduct(token, id).catch(() => {});
    }
  });

  test.beforeEach(async ({ page }) => {
    await page.goto('/admin/login');
    await page.getByLabel('Username').fill(ADMIN_USER);
    await page.getByLabel('Password').fill(ADMIN_PASSWORD);
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page).toHaveURL(/\/admin\/inventory$/);
  });

  test('stat tiles, top items and sales table match the sale just made', async ({ page, consoleErrors }) => {
    const today = todayISO();
    const baseline = await apiSalesSummary(token, today);

    // The top-items bar is capped at the top 5 SKUs by qty_sold for the day
    // (backend TOP_ITEMS_LIMIT), and this suite may have run many times
    // already today, each run adding a distinct new SKU. Sell a quantity far
    // beyond anything any other run could plausibly reach so this product is
    // deterministically ranked #1 regardless of how much history has piled up.
    const SALE_QTY = 100000;
    const name = `E2E Summary Widget ${uniqueSuffix()}`;
    const product = await apiCreateProduct(token, {
      sku: `E2E-SUM-${uniqueSuffix()}`,
      name,
      qty: SALE_QTY + 1,
      price_cents: 1000,
    });
    createdIds.push(product.id);

    await page.goto('/admin/sale');
    await page.getByLabel('Find product').fill(name);
    await selectProductByName(page.locator('select.input'), name);
    await page.getByLabel('Qty').fill(String(SALE_QTY));
    await page.getByRole('button', { name: 'Record sale' }).click();
    await expect(page.getByRole('status')).toBeVisible();

    await page.goto('/admin/summary');
    // Ensure we're on today (default) and data has loaded.
    await expect(page.locator('.stat-tile')).toHaveCount(3);

    const expectedRevenue = baseline.total_revenue_cents + SALE_QTY * 1000;
    const expectedItems = baseline.total_items_sold + SALE_QTY;
    const expectedTx = baseline.transaction_count + 1;

    const revenueTile = page.locator('.stat-tile', { hasText: 'Revenue' });
    const itemsTile = page.locator('.stat-tile', { hasText: 'Items sold' });
    const txTile = page.locator('.stat-tile', { hasText: 'Transactions' });

    await expect(revenueTile.locator('.stat-value')).toHaveText(formatCents(expectedRevenue));
    await expect(itemsTile.locator('.stat-value')).toHaveText(String(expectedItems));
    await expect(txTile.locator('.stat-value')).toHaveText(String(expectedTx));

    // Top items bar includes our product.
    await expect(page.locator('.top-item', { hasText: name })).toBeVisible();

    // Sales table includes a row for this sale.
    const saleRow = page.locator('tr', { hasText: product.sku });
    await expect(saleRow).toBeVisible();
    await expect(saleRow.getByText('$10.00')).toBeVisible();
    await expect(saleRow.getByText(formatCents(SALE_QTY * 1000))).toBeVisible();

    expect(consoleErrors).toEqual([]);
  });

  test('switching the date picker to an empty day shows zeros', async ({ page }) => {
    await page.goto('/admin/summary');
    // A day 10 years out has no sales recorded.
    await page.locator('input[type="date"]').fill('2036-06-01');

    const revenueTile = page.locator('.stat-tile', { hasText: 'Revenue' });
    const itemsTile = page.locator('.stat-tile', { hasText: 'Items sold' });
    const txTile = page.locator('.stat-tile', { hasText: 'Transactions' });

    await expect(revenueTile.locator('.stat-value')).toHaveText('$0.00');
    await expect(itemsTile.locator('.stat-value')).toHaveText('0');
    await expect(txTile.locator('.stat-value')).toHaveText('0');
    await expect(page.getByText('No sales recorded for 2036-06-01.')).toBeVisible();
  });
});
