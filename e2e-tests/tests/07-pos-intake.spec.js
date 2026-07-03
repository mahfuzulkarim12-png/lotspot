const { test, expect } = require('./fixtures');
const {
  uniqueSuffix,
  apiLogin,
  apiCreateProduct,
  apiDeleteProduct,
  apiPosSale,
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

    const res = await fetch('http://localhost:4322/api/pos/sales', {
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
});
