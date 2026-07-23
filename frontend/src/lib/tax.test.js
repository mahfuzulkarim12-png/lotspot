import { describe, expect, test } from 'vitest';
import { activeAccountsForCategory, computeCartTax, computeLineTax } from './tax';

const STATE = { id: 1, name: 'State Tax', jurisdiction: 'OK State', rate_bps: 825, effective_from: '2000-01-01', effective_to: null };
const CITY = { id: 2, name: 'City Tax', jurisdiction: 'Tulsa City', rate_bps: 200, effective_from: '2000-01-01', effective_to: null };
const EXPIRED = { id: 3, name: 'Expired Tax', jurisdiction: 'Old', rate_bps: 999, effective_from: '2000-01-01', effective_to: '2010-01-01' };
const FUTURE = { id: 4, name: 'Future Tax', jurisdiction: 'New', rate_bps: 999, effective_from: '2099-01-01', effective_to: null };

const CATEGORIES = [
  { id: 10, name: 'Taxed', tax_account_ids: [1, 2, 3, 4] },
  { id: 11, name: 'Untaxed', tax_account_ids: [] },
];

const ACCOUNTS = [STATE, CITY, EXPIRED, FUTURE];
const ACCOUNTS_BY_ID = new Map(ACCOUNTS.map((a) => [a.id, a]));
const TODAY = '2026-07-23';

describe('activeAccountsForCategory', () => {
  test('returns only accounts active as of the given day', () => {
    const active = activeAccountsForCategory(10, CATEGORIES, ACCOUNTS_BY_ID, TODAY);
    expect(active.map((a) => a.id).sort()).toEqual([1, 2]);
  });

  test('returns an empty list for a category with no mapped accounts', () => {
    expect(activeAccountsForCategory(11, CATEGORIES, ACCOUNTS_BY_ID, TODAY)).toEqual([]);
  });

  test('returns an empty list for an unknown or null category', () => {
    expect(activeAccountsForCategory(999, CATEGORIES, ACCOUNTS_BY_ID, TODAY)).toEqual([]);
    expect(activeAccountsForCategory(null, CATEGORIES, ACCOUNTS_BY_ID, TODAY)).toEqual([]);
  });
});

describe('computeLineTax', () => {
  test('produces one tax line per active account, integer cents only', () => {
    const lines = computeLineTax(1000, [STATE, CITY]);
    expect(lines).toEqual([
      { tax_account_id: 1, tax_account_name: 'State Tax', rate_bps: 825, tax_cents: 83 },
      { tax_account_id: 2, tax_account_name: 'City Tax', rate_bps: 200, tax_cents: 20 },
    ]);
  });
});

describe('computeCartTax', () => {
  test('sums per-jurisdiction tax across multiple cart lines', () => {
    const { taxCents, breakdown } = computeCartTax(
      [
        { tax_category_id: 10, line_total_cents: 1000 },
        { tax_category_id: 10, line_total_cents: 500 },
      ],
      CATEGORIES,
      ACCOUNTS,
      TODAY
    );

    expect(taxCents).toBe(83 + 20 + 41 + 10); // $10 line + $5 line, each at 8.25% + 2%
    expect(breakdown).toEqual([
      { tax_account_id: 2, tax_account_name: 'City Tax', rate_bps: 200, tax_cents: 30 },
      { tax_account_id: 1, tax_account_name: 'State Tax', rate_bps: 825, tax_cents: 124 },
    ]);
  });

  test('untaxed lines contribute zero tax and no breakdown rows', () => {
    const { taxCents, breakdown } = computeCartTax(
      [{ tax_category_id: 11, line_total_cents: 1000 }],
      CATEGORIES,
      ACCOUNTS,
      TODAY
    );
    expect(taxCents).toBe(0);
    expect(breakdown).toEqual([]);
  });
});
