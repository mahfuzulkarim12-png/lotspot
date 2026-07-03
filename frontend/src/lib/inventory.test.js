import { describe, expect, test } from 'vitest';
import { applyInventoryEvent, filterProducts, stockStatus } from './inventory';

const PRODUCTS = [
  { id: 1, sku: 'COKE-330', name: 'Coca-Cola 330ml', qty: 10, price_cents: 250 },
  { id: 2, sku: 'BREAD-01', name: 'White Bread', qty: 2, price_cents: 400 },
  { id: 3, sku: 'GUM-01', name: 'Mint Gum', qty: 0, price_cents: 150 },
];

describe('filterProducts', () => {
  test('matches name case-insensitively', () => {
    expect(filterProducts(PRODUCTS, 'cola')).toHaveLength(1);
    expect(filterProducts(PRODUCTS, 'COLA')[0].id).toBe(1);
  });

  test('matches sku', () => {
    expect(filterProducts(PRODUCTS, 'bread-01')[0].id).toBe(2);
  });

  test('empty or whitespace query keeps everything', () => {
    expect(filterProducts(PRODUCTS, '')).toHaveLength(3);
    expect(filterProducts(PRODUCTS, '   ')).toHaveLength(3);
  });

  test('no match yields empty list', () => {
    expect(filterProducts(PRODUCTS, 'sushi')).toEqual([]);
  });
});

describe('applyInventoryEvent', () => {
  test('created inserts sorted by name', () => {
    const created = { id: 4, sku: 'APL-01', name: 'Apple', qty: 5, price_cents: 80 };
    const next = applyInventoryEvent(PRODUCTS, { action: 'created', product: created });
    expect(next.map((p) => p.id)).toContain(4);
    expect(next[0].name).toBe('Apple');
    expect(PRODUCTS).toHaveLength(3); // immutability: original untouched
  });

  test('updated replaces the matching product', () => {
    const updated = { ...PRODUCTS[0], qty: 7 };
    const next = applyInventoryEvent(PRODUCTS, { action: 'updated', product: updated });
    expect(next.find((p) => p.id === 1).qty).toBe(7);
    expect(next).toHaveLength(3);
    expect(PRODUCTS.find((p) => p.id === 1).qty).toBe(10);
  });

  test('updated for an unseen product inserts it (late join)', () => {
    const stranger = { id: 9, sku: 'NEW-9', name: 'ZZZ Item', qty: 1, price_cents: 100 };
    const next = applyInventoryEvent(PRODUCTS, { action: 'updated', product: stranger });
    expect(next).toHaveLength(4);
  });

  test('deleted removes by product_id', () => {
    const next = applyInventoryEvent(PRODUCTS, { action: 'deleted', product_id: 2 });
    expect(next.map((p) => p.id)).toEqual([1, 3]);
  });

  test('unknown action and null list are left alone', () => {
    expect(applyInventoryEvent(PRODUCTS, { action: 'mystery' })).toBe(PRODUCTS);
    expect(applyInventoryEvent(null, { action: 'created', product: {} })).toBeNull();
  });
});

describe('stockStatus', () => {
  test('classifies stock levels', () => {
    expect(stockStatus(0)).toBe('out');
    expect(stockStatus(5)).toBe('low');
    expect(stockStatus(6)).toBe('ok');
    expect(stockStatus(100)).toBe('ok');
  });
});
