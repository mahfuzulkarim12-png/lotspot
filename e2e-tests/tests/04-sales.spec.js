const { test, expect } = require('./fixtures');
const {
  ADMIN_USER,
  ADMIN_PASSWORD,
  uniqueSuffix,
  apiLogin,
  apiCreateProduct,
  apiDeleteProduct,
  selectProductByName,
} = require('./helpers');

test.describe('Sales entry', () => {
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

  test('pick product, qty, total preview updates, Record sale succeeds', async ({ page, consoleErrors }) => {
    const name = `E2E Sale Widget ${uniqueSuffix()}`;
    const product = await apiCreateProduct(token, {
      sku: `E2E-SALE-${uniqueSuffix()}`,
      name,
      qty: 10,
      price_cents: 500,
    });
    createdIds.push(product.id);

    await page.goto('/admin/sale');
    await page.getByLabel('Find product').fill(name);
    await selectProductByName(page.locator('select.input'), name);
    await page.getByLabel('Qty').fill('3');

    await expect(page.getByText('$15.00')).toBeVisible();

    await page.getByRole('button', { name: 'Record sale' }).click();
    await expect(page.getByRole('status')).toContainText(`Recorded: 3 × ${name}`);
    await expect(page.getByRole('status')).toContainText('$15.00');

    // Stock decreased (10 - 3 = 7) — reflected back in inventory.
    await page.goto('/admin/inventory');
    const row = page.locator('tr', { hasText: product.sku });
    await expect(row.locator('td.col-num.num').first()).toHaveText('7');

    expect(consoleErrors).toEqual([]);
  });

  test('overselling (qty > stock) surfaces the 409 Insufficient stock message', async ({ page }) => {
    const name = `E2E Oversell Widget ${uniqueSuffix()}`;
    const product = await apiCreateProduct(token, {
      sku: `E2E-OVER-${uniqueSuffix()}`,
      name,
      qty: 2,
      price_cents: 250,
    });
    createdIds.push(product.id);

    await page.goto('/admin/sale');
    await page.getByLabel('Find product').fill(name);
    await selectProductByName(page.locator('select.input'), name);
    await page.getByLabel('Qty').fill('5');
    await page.getByRole('button', { name: 'Record sale' }).click();

    const alert = page.getByRole('alert');
    await expect(alert).toBeVisible();
    await expect(alert).toContainText('Insufficient stock');

    // Stock must be unchanged after the rejected sale.
    await page.goto('/admin/inventory');
    const row = page.locator('tr', { hasText: product.sku });
    await expect(row.locator('td.col-num.num').first()).toHaveText('2');
  });

  test('stock decreases live in a second tab while the sale happens', async ({ page, browser }) => {
    const name = `E2E Live Sale Widget ${uniqueSuffix()}`;
    const product = await apiCreateProduct(token, {
      sku: `E2E-LIVESALE-${uniqueSuffix()}`,
      name,
      qty: 6,
      price_cents: 199,
    });
    createdIds.push(product.id);

    const customerContext = await browser.newContext();
    const customerPage = await customerContext.newPage();
    try {
      await customerPage.goto('/');
      const customerCard = customerPage.locator('.product-card', { hasText: name });
      await expect(customerCard.getByText('6 left')).toBeVisible();

      await page.goto('/admin/sale');
      await page.getByLabel('Find product').fill(name);
      await selectProductByName(page.locator('select.input'), name);
      await page.getByLabel('Qty').fill('2');
      await page.getByRole('button', { name: 'Record sale' }).click();
      await expect(page.getByRole('status')).toBeVisible();

      await expect(customerCard.getByText('4 left')).toBeVisible({ timeout: 2000 });
    } finally {
      await customerContext.close();
    }
  });
});
