const { test, expect } = require('./fixtures');
const {
  BASE_URL,
  ADMIN_USER,
  ADMIN_PASSWORD,
  uniqueSuffix,
  apiLogin,
  apiCreateProduct,
  apiDeleteProduct,
  apiPosSale,
  apiSalesHistory,
  todayISO,
  formatCents,
} = require('./helpers');

test.describe('POS intake', () => {
  let token;
  let createdId;

  test.beforeAll(async () => {
    token = await apiLogin();
  });

  test.afterEach(async () => {
    if (createdId) {
      await apiDeleteProduct(token, createdId).catch(() => {});
      createdId = null;
    }
  });

  test('POST /api/pos/sales with X-API-Key drops stock live in the open customer view', async ({ page, consoleErrors }) => {
    const suffix = uniqueSuffix();
    const sku = `E2E-POS-${suffix}`;
    const name = `E2E POS Widget ${suffix}`;
    const product = await apiCreateProduct(token, { sku, name, qty: 15, price_cents: 275 });
    createdId = product.id;

    await page.goto('/');
    const card = page.locator('.product-card', { hasText: name });
    await expect(card.getByText('15 left')).toBeVisible();

    const { status, envelope } = await apiPosSale({ sku, qty: 5 });
    expect(status).toBe(201);
    expect(envelope.success).toBe(true);

    await expect(card.getByText('10 left')).toBeVisible({ timeout: 2000 });
    expect(consoleErrors).toEqual([]);
  });

  test('wrong API key is rejected', async () => {
    const suffix = uniqueSuffix();
    const sku = `E2E-POSBAD-${suffix}`;
    const product = await apiCreateProduct(token, {
      sku,
      name: `E2E POS Bad Key Widget ${suffix}`,
      qty: 5,
      price_cents: 100,
    });
    createdId = product.id;

    const res = await fetch(`${BASE_URL}/api/pos/sales`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': 'wrong-key' },
      body: JSON.stringify({ sku, qty: 1 }),
    });
    expect(res.status).toBe(401);
  });

  test('unknown SKU is rejected without side effects', async () => {
    const { status, envelope } = await apiPosSale({ sku: 'DOES-NOT-EXIST-SKU', qty: 1 });
    expect(status).toBeGreaterThanOrEqual(400);
    expect(envelope.success).toBe(false);
  });

  test('a simulated rapid barcode scan adds the matching product to the cart without manual search interaction', async ({ page }) => {
    const suffix = uniqueSuffix().slice(-6);
    const sku = `SCN${suffix}`;
    const name = `E2E Scan Widget ${suffix}`;
    const product = await apiCreateProduct(token, { sku, name, qty: 20, price_cents: 350 });
    createdId = product.id;

    await page.goto('/admin/login');
    await page.getByLabel('Username').fill(ADMIN_USER);
    await page.getByLabel('Password').fill(ADMIN_PASSWORD);
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page).toHaveURL(/\/admin\/inventory$/);
    await page.goto('/admin/pos');
    await expect(page).toHaveURL(/\/admin\/pos$/);

    // Simulate the keyboard-wedge burst a hardware scanner sends: a fast
    // run of keystrokes terminated by Enter, with no click on "Add to cart".
    const searchInput = page.getByLabel('Search products');
    await searchInput.pressSequentially(sku, { delay: 5 });
    await searchInput.press('Enter');

    await expect(page.getByText(`Scanned ${name} — added to cart.`)).toBeVisible();
    const cartLine = page.locator('.pos-cart-line', { hasText: name });
    await expect(cartLine).toBeVisible();
    await expect(cartLine.getByLabel('Qty')).toHaveValue('1');
    await expect(searchInput).toHaveValue('');
  });

  test('scanning an unknown SKU shows an error state and leaves the cart untouched', async ({ page }) => {
    await page.goto('/admin/login');
    await page.getByLabel('Username').fill(ADMIN_USER);
    await page.getByLabel('Password').fill(ADMIN_PASSWORD);
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page).toHaveURL(/\/admin\/inventory$/);
    await page.goto('/admin/pos');
    await expect(page).toHaveURL(/\/admin\/pos$/);

    const searchInput = page.getByLabel('Search products');
    const unknownSku = `SCNBAD${uniqueSuffix().slice(-6)}`;
    await searchInput.pressSequentially(unknownSku, { delay: 5 });
    await searchInput.press('Enter');

    await expect(page.getByRole('alert')).toContainText('No product matches scanned code');
    await expect(
      page.getByText('Add products from the search results or quick-select grid.')
    ).toBeVisible();
  });

  test('admin POS checkout updates the daily history feed in real time', async ({ browser }) => {
    const today = todayISO();
    const baseline = await apiSalesHistory(token, today, today);

    const soda = await apiCreateProduct(token, {
      sku: `E2E-POSUI-${uniqueSuffix()}-SODA`,
      name: `E2E POS Soda ${uniqueSuffix()}`,
      qty: 1000,
      price_cents: 250,
    });
    const chips = await apiCreateProduct(token, {
      sku: `E2E-POSUI-${uniqueSuffix()}-CHIPS`,
      name: `E2E POS Chips ${uniqueSuffix()}`,
      qty: 1000,
      price_cents: 400,
    });

    const context = await browser.newContext();
    const loginPage = await context.newPage();
    const historyPage = await context.newPage();

    try {
      await loginPage.goto('/admin/login');
      await loginPage.getByLabel('Username').fill(ADMIN_USER);
      await loginPage.getByLabel('Password').fill(ADMIN_PASSWORD);
      await loginPage.getByRole('button', { name: /sign in/i }).click();
      await expect(loginPage).toHaveURL(/\/admin\/inventory$/);
      await loginPage.goto('/admin/pos');
      await expect(loginPage).toHaveURL(/\/admin\/pos$/);

      await historyPage.goto('/admin/history');
      await expect(historyPage.getByText('Daily sales history')).toBeVisible();

      await loginPage.getByLabel('Search products').fill(soda.name);
      await loginPage.getByRole('button', { name: /Add to cart/i }).click();
      await loginPage.getByLabel('Search products').fill(chips.name);
      await loginPage.getByRole('button', { name: /Add to cart/i }).click();
      await loginPage.getByRole('button', { name: /Complete checkout/i }).click();
      await expect(loginPage.getByRole('status')).toContainText('Checkout complete');

      const baseDay = baseline.days[0] || { transaction_count: 0, total_items_sold: 0, total_revenue_cents: 0 };
      const expectedHistory = {
        transaction_count: baseDay.transaction_count + 1,
        total_items_sold: baseDay.total_items_sold + 2,
        total_revenue_cents: baseDay.total_revenue_cents + 650,
      };

      // exact: true avoids false matches against other cells in the row
      // (e.g. the date cell or the tax-collected cell containing the same digit).
      const historyRow = historyPage.locator('tr', { hasText: today });
      await expect(
        historyRow.getByText(String(expectedHistory.transaction_count), { exact: true })
      ).toBeVisible({ timeout: 3000 });
      await expect(
        historyRow.getByText(String(expectedHistory.total_items_sold), { exact: true })
      ).toBeVisible();
      await expect(
        historyRow.getByText(formatCents(expectedHistory.total_revenue_cents), { exact: true })
      ).toBeVisible();
    } finally {
      await context.close();
      await apiDeleteProduct(token, soda.id).catch(() => {});
      await apiDeleteProduct(token, chips.id).catch(() => {});
      createdId = null;
    }
  });
});
