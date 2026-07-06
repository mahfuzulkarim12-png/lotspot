const { test, expect } = require('./fixtures');
const { ADMIN_USER, ADMIN_PASSWORD, uniqueSuffix, apiLogin, apiDeleteProduct, stableRow } = require('./helpers');

test.describe('Real-time cross-tab updates (SSE)', () => {
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

  test('add/edit in admin reflects on customer view within 2s, and qty=0 hides it', async ({ browser }) => {
    const customerContext = await browser.newContext();
    const adminContext = await browser.newContext();
    const customerPage = await customerContext.newPage();
    const adminPage = await adminContext.newPage();

    try {
      await customerPage.goto('/');
      await expect(customerPage.locator('.live-dot')).toHaveText('Live', { timeout: 5000 });
      await expect(customerPage.locator('.live-dot')).toHaveClass(/is-live/);

      await adminPage.goto('/admin/login');
      await adminPage.getByLabel('Username').fill(ADMIN_USER);
      await adminPage.getByLabel('Password').fill(ADMIN_PASSWORD);
      await adminPage.getByRole('button', { name: /sign in/i }).click();
      await expect(adminPage).toHaveURL(/\/admin\/inventory$/);

      const sku = `E2E-RT-${uniqueSuffix()}`;
      const name = `E2E Realtime Widget ${uniqueSuffix()}`;

      await adminPage.locator('input[placeholder="COKE-330"]').fill(sku);
      await adminPage.locator('input[placeholder="Coca-Cola 330ml"]').fill(name);
      await adminPage.locator('input[placeholder="24"]').fill('4');
      await adminPage.locator('input[placeholder="2.50"]').fill('9.99');
      await adminPage.getByRole('button', { name: 'Add product' }).click();

      const adminRow = adminPage.locator('tr', { hasText: sku });
      await expect(adminRow).toBeVisible();

      const res = await adminPage.request.get('/api/products');
      const body = await res.json();
      createdId = body.data.find((p) => p.sku === sku).id;

      // Real-time propagation to the OTHER tab, no reload, within ~2s.
      const customerCard = customerPage.locator('.product-card', { hasText: name });
      await expect(customerCard).toBeVisible({ timeout: 2000 });
      await expect(customerCard.getByText('4 left')).toBeVisible();
      await expect(customerCard.getByText('$9.99')).toBeVisible();

      const editableAdminRow = await stableRow(adminPage, adminRow);

      // Edit qty down (still > 0) — customer view updates live.
      await editableAdminRow.getByRole('button', { name: 'Edit' }).click();
      await editableAdminRow.getByLabel('Qty').fill('1');
      await editableAdminRow.getByRole('button', { name: 'Save' }).click();
      await expect(customerCard.getByText('1 left')).toBeVisible({ timeout: 2000 });
      await expect(customerCard.locator('.badge')).toHaveText('Low stock');

      // Edit qty to 0 — product must disappear from the customer (in-stock only) view.
      await editableAdminRow.getByRole('button', { name: 'Edit' }).click();
      await editableAdminRow.getByLabel('Qty').fill('0');
      await editableAdminRow.getByRole('button', { name: 'Save' }).click();
      await expect(customerCard).toHaveCount(0, { timeout: 2000 });

      // But it still shows in the admin inventory table (out of stock, not deleted).
      await expect(editableAdminRow.locator('.badge')).toHaveText('Out');
    } finally {
      await customerContext.close();
      await adminContext.close();
    }
  });

  test('5 concurrent customer tabs all receive the same update, and the stream survives a heartbeat cycle', async ({ browser }) => {
    const TAB_COUNT = 5;
    const contexts = await Promise.all(Array.from({ length: TAB_COUNT }, () => browser.newContext()));
    const pages = await Promise.all(contexts.map((ctx) => ctx.newPage()));

    try {
      await Promise.all(pages.map((page) => page.goto('/')));
      await Promise.all(
        pages.map((page) => expect(page.locator('.live-dot')).toHaveText('Live', { timeout: 5000 }))
      );

      const sku = `E2E-RT5-${uniqueSuffix()}`;
      const name = `E2E Concurrent Widget ${uniqueSuffix()}`;
      const product = await require('./helpers').apiCreateProduct(token, {
        sku,
        name,
        qty: 6,
        price_cents: 499,
      });
      createdId = product.id;

      // Every tab subscribed to the SSE stream must independently observe the
      // same create event — not just the first or last one connected.
      await Promise.all(
        pages.map((page) =>
          expect(page.locator('.product-card', { hasText: name })).toBeVisible({ timeout: 2000 })
        )
      );

      // The default 15s heartbeat is overridden to 2s for this test run (see
      // LOTSPOT_SSE_HEARTBEAT) so we can prove the connection survives past a
      // keepalive tick — without the CI job idling 15+s per test.
      await pages[0].waitForTimeout(3500);

      await require('./helpers').apiRequest(`/api/products/${createdId}`, {
        method: 'PUT',
        token,
        body: { qty: 2 },
      });

      await Promise.all(
        pages.map((page) =>
          expect(page.locator('.product-card', { hasText: name }).getByText('2 left')).toBeVisible({
            timeout: 2000,
          })
        )
      );
    } finally {
      await Promise.all(contexts.map((ctx) => ctx.close()));
    }
  });
});
