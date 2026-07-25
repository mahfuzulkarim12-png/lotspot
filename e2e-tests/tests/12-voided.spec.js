const { test, expect } = require('./fixtures');
const {
  ADMIN_USER,
  ADMIN_PASSWORD,
  uniqueSuffix,
  apiLogin,
  apiCreateProduct,
  apiDeleteProduct,
  apiPosCheckout,
  apiVoidSale,
  apiVoidReceipt,
} = require('./helpers');

function daysAgoISO(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// The receipts table shows the first 8 hex chars of the transaction_id
// (see VoidedPanel.jsx shortReceiptId), so tests match on that prefix.
function shortReceiptId(transactionId) {
  return transactionId.slice(0, 8);
}

async function login(page) {
  await page.goto('/admin/login');
  await page.getByLabel('Username').fill(ADMIN_USER);
  await page.getByLabel('Password').fill(ADMIN_PASSWORD);
  await page.getByRole('button', { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/admin\/inventory$/);
}

// The Voided panel renders two `.table-card` tables: receipts, then items.
// Scoping to these avoids ambiguous matches against each other's rows.
function receiptsTable(page) {
  return page.locator('.table-card').first();
}
function itemsTable(page) {
  return page.locator('.table-card').nth(1);
}

test.describe('Voided sales admin panel', () => {
  let token;
  const createdProductIds = [];

  test.beforeAll(async () => {
    token = await apiLogin();
  });

  test.afterEach(async () => {
    while (createdProductIds.length) {
      const id = createdProductIds.pop();
      await apiDeleteProduct(token, id).catch(() => {});
    }
  });

  test('a fully voided receipt appears under Voided receipts, expand/collapse reveals its line items, and the search box narrows to it', async ({ page, consoleErrors }) => {
    const suffix = uniqueSuffix();
    const productA = await apiCreateProduct(token, {
      sku: `E2E-VOID-A-${suffix}`,
      name: `E2E Void Widget A ${suffix}`,
      qty: 10,
      price_cents: 300,
    });
    const productB = await apiCreateProduct(token, {
      sku: `E2E-VOID-B-${suffix}`,
      name: `E2E Void Widget B ${suffix}`,
      qty: 10,
      price_cents: 450,
    });
    createdProductIds.push(productA.id, productB.id);

    const checkout = await apiPosCheckout(token, {
      items: [
        { product_id: productA.id, qty: 1 },
        { product_id: productB.id, qty: 1 },
      ],
      payment_method: 'cash',
    });
    const receiptId = shortReceiptId(checkout.transaction_id);
    await apiVoidReceipt(token, checkout.transaction_id, 'E2E full receipt void');

    await login(page);
    await page.goto('/admin/voided');

    const receiptRow = receiptsTable(page).locator('tbody tr', { hasText: receiptId });
    await expect(receiptRow).toBeVisible();
    await expect(receiptsTable(page).locator('tbody tr', { hasText: productA.name })).toHaveCount(0);

    // Expand reveals both line items in the nested line-items table.
    await receiptRow.getByRole('button', { name: 'Expand line items' }).click();
    await expect(receiptsTable(page).locator('tbody tr', { hasText: productA.name })).toContainText(productA.sku);
    await expect(receiptsTable(page).locator('tbody tr', { hasText: productB.name })).toContainText(productB.sku);

    // Collapse hides them again.
    await receiptRow.getByRole('button', { name: 'Collapse line items' }).click();
    await expect(receiptsTable(page).locator('tbody tr', { hasText: productA.name })).toHaveCount(0);

    // Search box narrows results to this receipt id.
    await page.getByLabel('Receipt ID').fill(receiptId);
    await expect(receiptRow).toBeVisible();

    // An unrelated search excludes it and shows the empty state.
    await page.getByLabel('Receipt ID').fill('no-such-receipt-zzz');
    await expect(receiptsTable(page).locator('tbody tr', { hasText: receiptId })).toHaveCount(0);
    await expect(page.getByText('No voided receipts found for this range.')).toBeVisible();

    // Clearing the search brings it back.
    await page.getByLabel('Receipt ID').fill('');
    await expect(receiptRow).toBeVisible();

    expect(consoleErrors).toEqual([]);
  });

  test('voiding a single item from a multi-item receipt appears under Voided items, not Voided receipts', async ({ page }) => {
    const suffix = uniqueSuffix();
    const productA = await apiCreateProduct(token, {
      sku: `E2E-VOIDITEM-A-${suffix}`,
      name: `E2E Void Item Widget A ${suffix}`,
      qty: 10,
      price_cents: 200,
    });
    const productB = await apiCreateProduct(token, {
      sku: `E2E-VOIDITEM-B-${suffix}`,
      name: `E2E Void Item Widget B ${suffix}`,
      qty: 10,
      price_cents: 350,
    });
    createdProductIds.push(productA.id, productB.id);

    const checkout = await apiPosCheckout(token, {
      items: [
        { product_id: productA.id, qty: 1 },
        { product_id: productB.id, qty: 1 },
      ],
      payment_method: 'card',
    });
    const receiptId = shortReceiptId(checkout.transaction_id);
    const saleToVoid = checkout.line_items.find((line) => line.product_id === productA.id);
    await apiVoidSale(token, saleToVoid.id, 'Damaged in transit');

    await login(page);
    await page.goto('/admin/voided');

    const itemRow = itemsTable(page).locator('tbody tr', { hasText: productA.name });
    await expect(itemRow).toBeVisible();
    await expect(itemRow).toContainText(receiptId);
    await expect(itemRow).toContainText('Damaged in transit');

    // Sibling item is still active, so the receipt must not show as fully voided,
    // and the untouched sibling must not appear as a voided item either.
    await expect(receiptsTable(page).locator('tbody tr', { hasText: receiptId })).toHaveCount(0);
    await expect(itemsTable(page).locator('tbody tr', { hasText: productB.name })).toHaveCount(0);
  });

  test('a custom date range excluding today hides a voided receipt, and widening the range brings it back', async ({ page }) => {
    const suffix = uniqueSuffix();
    const product = await apiCreateProduct(token, {
      sku: `E2E-VOIDRANGE-${suffix}`,
      name: `E2E Void Range Widget ${suffix}`,
      qty: 5,
      price_cents: 500,
    });
    createdProductIds.push(product.id);

    const checkout = await apiPosCheckout(token, {
      items: [{ product_id: product.id, qty: 1 }],
      payment_method: 'cash',
    });
    const receiptId = shortReceiptId(checkout.transaction_id);
    await apiVoidReceipt(token, checkout.transaction_id, 'E2E range-filter void');

    await login(page);
    await page.goto('/admin/voided');

    const receiptRow = receiptsTable(page).locator('tbody tr', { hasText: receiptId });
    await expect(receiptRow).toBeVisible();

    // A custom range entirely before today excludes the receipt (filtered by sold_at).
    await page.getByLabel('Start').fill(daysAgoISO(10));
    await page.getByLabel('End').fill(daysAgoISO(5));
    await expect(receiptsTable(page).locator('tbody tr', { hasText: receiptId })).toHaveCount(0);
    await expect(page.getByText('No voided receipts found for this range.')).toBeVisible();

    // Switching back to the "Last 7 days" preset (which includes today) brings it back.
    await page.getByLabel('Range').selectOption('7');
    await expect(receiptRow).toBeVisible();
  });
});
