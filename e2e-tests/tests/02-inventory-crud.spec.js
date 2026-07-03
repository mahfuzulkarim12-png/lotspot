const { test, expect } = require('./fixtures');
const { ADMIN_USER, ADMIN_PASSWORD, uniqueSuffix, apiLogin, apiDeleteProduct, stableRow } = require('./helpers');

test.describe('Inventory CRUD', () => {
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

  test.beforeEach(async ({ page }) => {
    await page.goto('/admin/login');
    await page.getByLabel('Username').fill(ADMIN_USER);
    await page.getByLabel('Password').fill(ADMIN_PASSWORD);
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page).toHaveURL(/\/admin\/inventory$/);
  });

  test('add product via inline form shows correct $ formatting and stock badge', async ({ page, consoleErrors }) => {
    const sku = `E2E-ADD-${uniqueSuffix()}`;
    const row = page.locator('tr', { hasText: sku });

    await page.locator('input[placeholder="COKE-330"]').fill(sku);
    await page.locator('input[placeholder="Coca-Cola 330ml"]').fill('E2E Test Widget');
    await page.locator('input[placeholder="24"]').fill('12');
    await page.locator('input[placeholder="2.50"]').fill('2.50');
    await page.getByRole('button', { name: 'Add product' }).click();

    await expect(row).toBeVisible();
    await expect(row.getByText('$2.50')).toBeVisible();
    await expect(row.locator('td.col-num.num').first()).toHaveText('12');
    await expect(row.locator('.badge')).toHaveText('In stock');

    const productRow = await row.evaluate((tr) => tr.outerHTML);
    expect(productRow).toContain(sku);

    // capture id for cleanup via the API (list + find by sku)
    const res = await page.request.get('/api/products');
    const body = await res.json();
    const created = body.data.find((p) => p.sku === sku);
    createdId = created.id;

    expect(consoleErrors).toEqual([]);
  });

  test('edit qty/price inline with Save persists the change', async ({ page }) => {
    const sku = `E2E-EDIT-${uniqueSuffix()}`;
    await page.locator('input[placeholder="COKE-330"]').fill(sku);
    await page.locator('input[placeholder="Coca-Cola 330ml"]').fill('E2E Edit Widget');
    await page.locator('input[placeholder="24"]').fill('5');
    await page.locator('input[placeholder="2.50"]').fill('1.00');
    await page.getByRole('button', { name: 'Add product' }).click();

    const row = page.locator('tr', { hasText: sku });
    await expect(row).toBeVisible();

    const res = await page.request.get('/api/products');
    const body = await res.json();
    createdId = body.data.find((p) => p.sku === sku).id;

    const editableRow = await stableRow(page, row);
    await editableRow.getByRole('button', { name: 'Edit' }).click();
    await editableRow.getByLabel('Qty').fill('20');
    await editableRow.getByLabel('Price').fill('3.75');
    await editableRow.getByRole('button', { name: 'Save' }).click();

    await expect(editableRow.getByText('$3.75')).toBeVisible();
    await expect(editableRow.locator('td.col-num.num').first()).toHaveText('20');
  });

  test('delete shows confirm dialog and removes the row', async ({ page }) => {
    const sku = `E2E-DEL-${uniqueSuffix()}`;
    await page.locator('input[placeholder="COKE-330"]').fill(sku);
    await page.locator('input[placeholder="Coca-Cola 330ml"]').fill('E2E Delete Widget');
    await page.locator('input[placeholder="24"]').fill('3');
    await page.locator('input[placeholder="2.50"]').fill('0.99');
    await page.getByRole('button', { name: 'Add product' }).click();

    const row = page.locator('tr', { hasText: sku });
    await expect(row).toBeVisible();

    let dialogMessage = '';
    page.once('dialog', (dialog) => {
      dialogMessage = dialog.message();
      dialog.accept();
    });
    await row.getByRole('button', { name: 'Delete' }).click();

    await expect(row).toHaveCount(0);
    expect(dialogMessage).toContain('Delete');
    expect(dialogMessage).toContain(sku);
    createdId = null; // already deleted, nothing to clean up
  });
});
