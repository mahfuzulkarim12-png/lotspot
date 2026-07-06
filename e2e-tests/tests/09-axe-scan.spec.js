const { test, expect } = require('./fixtures');
const AxeBuilder = require('@axe-core/playwright').default;
const { ADMIN_USER, ADMIN_PASSWORD } = require('./helpers');

// WCAG 2.2 A/AA ruleset, run against the app's key screens. Best-practice
// rules are excluded on purpose — they flag opinionated style choices, not
// accessibility failures, and would make this suite noisy.
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

async function scan(page) {
  return new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
}

test.describe('Automated accessibility scan (axe-core)', () => {
  test('customer product view has no WCAG 2.2 violations', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.product-grid')).toBeVisible();
    const results = await scan(page);
    expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
  });

  test('admin login screen has no WCAG 2.2 violations', async ({ page }) => {
    await page.goto('/admin/login');
    const results = await scan(page);
    expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
  });

  test('admin inventory screen has no WCAG 2.2 violations', async ({ page }) => {
    await page.goto('/admin/login');
    await page.getByLabel('Username').fill(ADMIN_USER);
    await page.getByLabel('Password').fill(ADMIN_PASSWORD);
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page).toHaveURL(/\/admin\/inventory$/);

    const results = await scan(page);
    expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
  });
});

test.describe('Reduced motion', () => {
  test.use({ reducedMotion: 'reduce' });

  test('animations and transitions collapse to near-zero duration', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.product-grid')).toBeVisible();

    const durations = await page.evaluate(() => {
      const sample = document.querySelector('.product-grid') || document.body;
      const style = window.getComputedStyle(sample);
      return {
        animationDuration: style.animationDuration,
        transitionDuration: style.transitionDuration,
      };
    });

    const parseMs = (value) => {
      const first = value.split(',')[0].trim();
      return first.endsWith('ms') ? parseFloat(first) : parseFloat(first) * 1000;
    };

    expect(parseMs(durations.animationDuration)).toBeLessThanOrEqual(1);
    expect(parseMs(durations.transitionDuration)).toBeLessThanOrEqual(1);
  });
});
