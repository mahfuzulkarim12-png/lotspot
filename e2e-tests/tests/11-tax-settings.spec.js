const { test, expect } = require('./fixtures');
const {
  ADMIN_USER,
  ADMIN_PASSWORD,
  uniqueSuffix,
  apiLogin,
  apiCreateProduct,
  apiDeleteProduct,
  apiListProducts,
  apiListTaxAccounts,
  apiDeleteTaxAccount,
  apiListTaxCategories,
  todayISO,
  stableRow,
} = require('./helpers');

const GENERAL_MERCHANDISE = 'General Merchandise';
const PREPARED_FOOD = 'Prepared Food';
const FOOD_INGREDIENTS = 'Food & Food Ingredients';

function daysAgoISO(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

async function login(page) {
  await page.goto('/admin/login');
  await page.getByLabel('Username').fill(ADMIN_USER);
  await page.getByLabel('Password').fill(ADMIN_PASSWORD);
  await page.getByRole('button', { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/admin\/inventory$/);
}

// The tax-settings page renders two `.table-card` tables: tax accounts, then
// the category/account mapping grid. Scoping to these avoids ambiguous
// matches, since an account's name also appears as a `<th>` in the mapping
// table and a category's name appears in both the mapping table and the
// inventory page.
function accountsTable(page) {
  return page.locator('.table-card').first();
}
function categoryMappingTable(page) {
  return page.locator('.table-card').nth(1);
}

async function addTaxAccount(page, { name, jurisdiction, ratePercent, effectiveFrom, effectiveTo }) {
  await page.getByLabel('Name').fill(name);
  await page.getByLabel('Jurisdiction').fill(jurisdiction);
  if (ratePercent !== undefined) await page.getByLabel('Rate %').fill(ratePercent);
  if (effectiveFrom !== undefined) await page.getByLabel('Effective from').fill(effectiveFrom);
  if (effectiveTo !== undefined) await page.getByLabel('Effective to').fill(effectiveTo);
  await page.getByRole('button', { name: 'Add tax account' }).click();
}

// Sets a product's tax category via the inventory table's inline edit form.
// Uses stableRow because switching a row into edit mode replaces its text
// cells with <input>/<select> elements, so a hasText-based row locator loses
// its match the instant "Edit" is clicked (see helpers.js:stableRow).
async function setProductTaxCategory(page, productName, categoryLabel) {
  const row = await stableRow(page, page.locator('tbody tr', { hasText: productName }));
  await row.getByRole('button', { name: 'Edit' }).click();
  await row.getByLabel('Tax category').selectOption({ label: categoryLabel });
  await row.getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('tbody tr', { hasText: productName })).toContainText(categoryLabel);
}

async function addProductToCart(page, productName) {
  await page.getByLabel('Search products').fill(productName);
  await page.getByRole('button', { name: /Add to cart/i }).click();
  await expect(page.locator('.pos-cart-line', { hasText: productName })).toBeVisible();
}

function cartTotals(page) {
  const rows = page.locator('.pos-total-row');
  return {
    subtotal: rows.nth(0).locator('.num'),
    tax: rows.nth(1).locator('.num'),
    total: rows.nth(2).locator('.num'),
  };
}

test.describe('Tax settings and live POS tax preview', () => {
  let token;

  test.beforeAll(async () => {
    token = await apiLogin();
  });

  test('admin configures a rate, maps it to a category, and the customer sees it on the cart preview and receipt', async ({ page, consoleErrors }) => {
    const suffix = uniqueSuffix();
    const accountName = `E2E Tax Account ${suffix}`;
    const jurisdiction = `E2E Jurisdiction ${suffix}`;
    const productName = `E2E Taxed Widget ${suffix}`;
    const sku = `E2E-TAX-${suffix}`;
    const today = todayISO();

    const categories = await apiListTaxCategories(token);
    const generalMerchandise = categories.find((c) => c.name === GENERAL_MERCHANDISE);
    expect(generalMerchandise).toBeTruthy();

    let productId = null;
    let taxAccountId = null;

    try {
      // 1. Admin adds a tax account on /admin/tax-settings.
      await login(page);
      await page.goto('/admin/tax-settings');
      await expect(page.getByRole('heading', { name: 'Tax settings' })).toBeVisible();

      await addTaxAccount(page, {
        name: accountName,
        jurisdiction,
        ratePercent: '8.25',
        effectiveFrom: today,
      });

      const accountRow = accountsTable(page).locator('tr', { hasText: accountName });
      await expect(accountRow).toBeVisible();
      await expect(accountRow).toContainText(jurisdiction);
      await expect(accountRow).toContainText('8.25%');
      await expect(accountRow).toContainText(today);

      const accounts = await apiListTaxAccounts(token);
      const createdAccount = accounts.find((a) => a.name === accountName);
      expect(createdAccount).toBeTruthy();
      taxAccountId = createdAccount.id;

      // 2. Admin creates a product on /admin/inventory. It defaults to
      // General Merchandise, then the admin switches it to Prepared Food via
      // the tax category selector so this test's tax mapping never touches
      // the default category other specs' ad-hoc products rely on.
      await page.goto('/admin/inventory');
      const categorySelect = page.locator('form.form-card select.input');
      await expect(categorySelect).toHaveValue(String(generalMerchandise.id));

      await categorySelect.selectOption({ label: PREPARED_FOOD });
      await page.getByLabel('SKU').fill(sku);
      await page.getByLabel('Name').fill(productName);
      await page.getByLabel('Qty').fill('50');
      await page.getByLabel('Price').fill('10.00');
      await page.getByRole('button', { name: 'Add product' }).click();

      const productRow = page.locator('tbody tr', { hasText: productName });
      await expect(productRow).toBeVisible();
      await expect(productRow).toContainText(PREPARED_FOOD);

      const foundProducts = await apiListProducts(sku);
      const createdProduct = foundProducts.find((p) => p.sku === sku);
      expect(createdProduct).toBeTruthy();
      productId = createdProduct.id;

      // 3. Back on tax settings, map the new account to Prepared Food and
      // confirm the checkbox state survives a reload.
      await page.goto('/admin/tax-settings');
      const mappingRow = categoryMappingTable(page).locator('tr', { hasText: PREPARED_FOOD });
      const mappingCheckbox = mappingRow.getByLabel(`${PREPARED_FOOD} applies ${accountName}`);
      // toggleCategoryAccount is a controlled checkbox whose `checked` prop
      // only flips after an async PUT + reload, so use click() (fire and
      // forget) followed by a polling assertion rather than check(), which
      // verifies the DOM flipped synchronously right after the click.
      await mappingCheckbox.click();
      await expect(mappingCheckbox).toBeChecked();

      await page.reload();
      const mappingRowAfterReload = categoryMappingTable(page).locator('tr', { hasText: PREPARED_FOOD });
      const mappingCheckboxAfterReload = mappingRowAfterReload.getByLabel(
        `${PREPARED_FOOD} applies ${accountName}`
      );
      await expect(mappingCheckboxAfterReload).toBeChecked();

      // 4. Ring up the product in the live POS UI and check the cart preview.
      // $10.00 subtotal at 8.25% (825 bps), round-half-up -> 83 cents tax.
      await page.goto('/admin/pos');
      await addProductToCart(page, productName);

      const totals = cartTotals(page);
      await expect(totals.subtotal).toHaveText('$10.00');
      await expect(totals.tax).toHaveText('$0.83');
      await expect(totals.total).toHaveText('$10.83');

      // 5. Complete checkout and confirm the receipt's per-jurisdiction breakdown.
      await page.getByRole('button', { name: /Complete checkout/i }).click();
      const receipt = page.locator('.pos-receipt');
      await expect(receipt).toBeVisible();
      await expect(receipt).toContainText('Subtotal $10.00');
      await expect(receipt).toContainText('Tax $0.83');
      await expect(receipt).toContainText('Total $10.83');

      const breakdownRow = receipt.locator('.pos-receipt-breakdown-row', { hasText: accountName });
      await expect(breakdownRow).toBeVisible();
      await expect(breakdownRow.locator('.num')).toHaveText('$0.83');

      expect(consoleErrors).toEqual([]);
    } finally {
      // 6. Cleanup via the API. Deleting the tax account cascades the
      // category/account mapping row automatically (ON DELETE CASCADE, see
      // backend/db.py PRAGMA foreign_keys = ON).
      if (productId) await apiDeleteProduct(token, productId).catch(() => {});
      if (taxAccountId) await apiDeleteTaxAccount(token, taxAccountId).catch(() => {});
    }
  });

  test('edge case: submitting a tax account with a blank rate is rejected client-side', async ({ page }) => {
    const suffix = uniqueSuffix();
    const name = `E2E Invalid Rate ${suffix}`;

    await login(page);
    await page.goto('/admin/tax-settings');

    await addTaxAccount(page, {
      name,
      jurisdiction: 'Nowhere',
      effectiveFrom: todayISO(),
      // ratePercent intentionally omitted
    });

    await expect(page.getByRole('alert')).toContainText('Rate must be a valid percentage');
    await expect(accountsTable(page).locator('tr', { hasText: name })).toHaveCount(0);
  });

  test('edge case: a product in a category with no mapped tax account shows zero tax', async ({ page }) => {
    const suffix = uniqueSuffix();
    const product = await apiCreateProduct(token, {
      sku: `E2E-NOTAX-${suffix}`,
      name: `E2E Untaxed Widget ${suffix}`,
      qty: 20,
      price_cents: 500,
    });

    try {
      const categories = await apiListTaxCategories(token);
      const foodIngredients = categories.find((c) => c.name === FOOD_INGREDIENTS);
      expect(foodIngredients).toBeTruthy();
      expect(foodIngredients.tax_account_ids).toEqual([]);

      await login(page);
      await page.goto('/admin/inventory');
      await setProductTaxCategory(page, product.name, FOOD_INGREDIENTS);

      await page.goto('/admin/pos');
      await addProductToCart(page, product.name);

      const totals = cartTotals(page);
      await expect(totals.subtotal).toHaveText('$5.00');
      await expect(totals.tax).toHaveText('$0.00');
      await expect(totals.total).toHaveText('$5.00');
    } finally {
      await apiDeleteProduct(token, product.id).catch(() => {});
    }
  });

  test('edge case: an expired tax account (effective_to in the past) does not apply tax', async ({ page }) => {
    const suffix = uniqueSuffix();
    const accountName = `E2E Expired Account ${suffix}`;
    const product = await apiCreateProduct(token, {
      sku: `E2E-EXP-${suffix}`,
      name: `E2E Expired Rate Widget ${suffix}`,
      qty: 20,
      price_cents: 750,
    });

    let taxAccountId = null;
    try {
      const categories = await apiListTaxCategories(token);
      const foodIngredients = categories.find((c) => c.name === FOOD_INGREDIENTS);
      expect(foodIngredients).toBeTruthy();

      await login(page);

      // Assign the product to Food & Food Ingredients.
      await page.goto('/admin/inventory');
      await setProductTaxCategory(page, product.name, FOOD_INGREDIENTS);

      // Add a tax account whose effective_to is yesterday, so it's expired
      // as of today, then map it to Food & Food Ingredients.
      await page.goto('/admin/tax-settings');
      await addTaxAccount(page, {
        name: accountName,
        jurisdiction: 'Expired State',
        ratePercent: '9.00',
        effectiveFrom: daysAgoISO(30),
        effectiveTo: daysAgoISO(1),
      });
      await expect(accountsTable(page).locator('tr', { hasText: accountName })).toBeVisible();

      const accounts = await apiListTaxAccounts(token);
      const createdAccount = accounts.find((a) => a.name === accountName);
      expect(createdAccount).toBeTruthy();
      taxAccountId = createdAccount.id;

      const mappingRow = categoryMappingTable(page).locator('tr', { hasText: FOOD_INGREDIENTS });
      const mappingCheckbox = mappingRow.getByLabel(`${FOOD_INGREDIENTS} applies ${accountName}`);
      await mappingCheckbox.click();
      await expect(mappingCheckbox).toBeChecked();

      // Even though the account is mapped, it's expired, so the cart shows no tax.
      await page.goto('/admin/pos');
      await addProductToCart(page, product.name);

      const totals = cartTotals(page);
      await expect(totals.subtotal).toHaveText('$7.50');
      await expect(totals.tax).toHaveText('$0.00');
      await expect(totals.total).toHaveText('$7.50');
    } finally {
      if (taxAccountId) await apiDeleteTaxAccount(token, taxAccountId).catch(() => {});
      await apiDeleteProduct(token, product.id).catch(() => {});
    }
  });
});
