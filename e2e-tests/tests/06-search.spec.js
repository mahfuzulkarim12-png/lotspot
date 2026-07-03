const { test, expect } = require('./fixtures');
const { uniqueSuffix, apiLogin, apiCreateProduct, apiDeleteProduct } = require('./helpers');

test.describe('Customer search', () => {
  let token;
  const createdIds = [];
  let uniqueName;
  let uniqueSku;

  test.beforeAll(async () => {
    token = await apiLogin();
    const suffix = uniqueSuffix();
    uniqueName = `Zzzyx Search Widget ${suffix}`;
    uniqueSku = `E2E-SRCH-${suffix}`;
    const product = await apiCreateProduct(token, {
      sku: uniqueSku,
      name: uniqueName,
      qty: 9,
      price_cents: 350,
    });
    createdIds.push(product.id);
  });

  test.afterAll(async () => {
    for (const id of createdIds) {
      await apiDeleteProduct(token, id).catch(() => {});
    }
  });

  test('search narrows by name', async ({ page, consoleErrors }) => {
    await page.goto('/');
    const search = page.getByLabel('Search products');
    await search.fill(uniqueName.split(' ')[0]);
    await expect(page.locator('.product-card', { hasText: uniqueName })).toBeVisible();
    // Narrowed — not showing an unrelated seeded product name.
    await expect(page.locator('.product-card')).toHaveCount(1);
    expect(consoleErrors).toEqual([]);
  });

  test('search narrows by SKU', async ({ page }) => {
    await page.goto('/');
    const search = page.getByLabel('Search products');
    await search.fill(uniqueSku);
    await expect(page.locator('.product-card', { hasText: uniqueName })).toBeVisible();
    await expect(page.locator('.product-card')).toHaveCount(1);
  });

  test('empty-state message shows for no matches', async ({ page }) => {
    await page.goto('/');
    const search = page.getByLabel('Search products');
    await search.fill('no-such-product-xyz-zzz');
    await expect(page.getByText(/Nothing in stock matches/)).toBeVisible();
    await expect(page.locator('.product-card')).toHaveCount(0);
  });
});
