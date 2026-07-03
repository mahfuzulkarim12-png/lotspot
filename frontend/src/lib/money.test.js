import { describe, expect, test } from 'vitest';
import { dollarsToCents, formatCents } from './money';

describe('formatCents', () => {
  test('formats cents as grouped dollars', () => {
    expect(formatCents(450)).toBe('$4.50');
    expect(formatCents(0)).toBe('$0.00');
    expect(formatCents(5)).toBe('$0.05');
    expect(formatCents(123456)).toBe('$1,234.56');
  });

  test('renders invalid input as $0.00 instead of crashing', () => {
    expect(formatCents(undefined)).toBe('$0.00');
    expect(formatCents(NaN)).toBe('$0.00');
  });
});

describe('dollarsToCents', () => {
  test('parses plain and decorated amounts', () => {
    expect(dollarsToCents('4.50')).toBe(450);
    expect(dollarsToCents('$4.50')).toBe(450);
    expect(dollarsToCents('1,234.56')).toBe(123456);
    expect(dollarsToCents('2')).toBe(200);
    expect(dollarsToCents('0.1')).toBe(10);
    expect(dollarsToCents(3)).toBe(300);
  });

  test('avoids float rounding drift', () => {
    expect(dollarsToCents('19.99')).toBe(1999);
    expect(dollarsToCents('0.29')).toBe(29);
  });

  test('rejects non-amounts', () => {
    expect(dollarsToCents('')).toBeNull();
    expect(dollarsToCents('abc')).toBeNull();
    expect(dollarsToCents('-5')).toBeNull();
    expect(dollarsToCents('1.2.3')).toBeNull();
    expect(dollarsToCents('.')).toBeNull();
    expect(dollarsToCents(null)).toBeNull();
  });
});
