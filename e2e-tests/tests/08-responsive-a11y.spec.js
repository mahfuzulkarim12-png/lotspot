const { test, expect } = require('./fixtures');
const { ADMIN_USER, ADMIN_PASSWORD } = require('./helpers');

const VIEWPORTS = [
  { name: '375px (mobile)', width: 375, height: 812 },
  { name: '1440px (desktop)', width: 1440, height: 900 },
];

for (const viewport of VIEWPORTS) {
  test.describe(`Responsive @ ${viewport.name}`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test('customer view has no horizontal overflow', async ({ page }) => {
      await page.goto('/');
      await expect(page.locator('.product-grid')).toBeVisible();
      const { scrollWidth, clientWidth } = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 1);
    });

    test('admin inventory table has no horizontal page overflow', async ({ page }) => {
      await page.goto('/admin/login');
      await page.getByLabel('Username').fill(ADMIN_USER);
      await page.getByLabel('Password').fill(ADMIN_PASSWORD);
      await page.getByRole('button', { name: /sign in/i }).click();
      await expect(page).toHaveURL(/\/admin\/inventory$/);

      const { scrollWidth, clientWidth } = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 1);
    });
  });
}

test.describe('Keyboard accessibility', () => {
  test('login inputs and submit button show a visible focus state', async ({ page }) => {
    await page.goto('/admin/login');

    await page.keyboard.press('Tab');
    const usernameFocused = await page.evaluate(() => document.activeElement?.getAttribute('autocomplete'));
    expect(usernameFocused).toBe('username');
    await expectVisibleFocusRing(page);

    await page.keyboard.press('Tab');
    const passwordFocused = await page.evaluate(() => document.activeElement?.getAttribute('autocomplete'));
    expect(passwordFocused).toBe('current-password');
    await expectVisibleFocusRing(page);

    // WebKit's default Tab order (matching real Safari) skips buttons unless
    // "Full Keyboard Access" is enabled, so a third Tab press won't reliably
    // land on the submit button there. Focus it directly instead to confirm
    // it's reachable and shows a visible ring, independent of tab-stop order.
    const submitButton = page.getByRole('button', { name: /sign in/i });
    await submitButton.focus();
    await expect(submitButton).toBeFocused();
    await expectVisibleFocusRing(page);
  });

  test('customer search input is reachable and focusable by keyboard', async ({ page }) => {
    await page.goto('/');
    const search = page.getByLabel('Search products');
    await search.focus();
    await expect(search).toBeFocused();
    await expectVisibleFocusRing(page);
  });
});

async function expectVisibleFocusRing(page) {
  const outline = await page.evaluate(() => {
    const el = document.activeElement;
    if (!el) return null;
    const style = window.getComputedStyle(el);
    return {
      outlineStyle: style.outlineStyle,
      outlineWidth: style.outlineWidth,
      boxShadow: style.boxShadow,
    };
  });
  expect(outline).not.toBeNull();
  const hasOutline = outline.outlineStyle !== 'none' && parseFloat(outline.outlineWidth) > 0;
  const hasBoxShadow = outline.boxShadow && outline.boxShadow !== 'none';
  expect(hasOutline || hasBoxShadow).toBe(true);
}
