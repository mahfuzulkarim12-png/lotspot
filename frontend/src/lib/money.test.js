import { describe, expect, test } from 'vitest';
import { bpsToPercent, dollarsToCents, formatCents, percentToBps, roundHalfUpBps } from './money';

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

describe('roundHalfUpBps', () => {
  test('rounds an exact half-cent tie up', () => {
    expect(roundHalfUpBps(2, 2500)).toBe(1); // 2c * 25% = 0.5c -> 1c
  });

  test('rounds a below-half remainder down', () => {
    expect(roundHalfUpBps(1, 4999)).toBe(0); // 1c * 49.99% = 0.4999c -> 0c
  });

  test('computes whole-cent results exactly with no drift', () => {
    expect(roundHalfUpBps(1000, 825)).toBe(83); // $10.00 * 8.25% = 82.5c -> 83c
    expect(roundHalfUpBps(1000, 200)).toBe(20); // $10.00 * 2% = 20c exactly
  });

  test('a zero rate always yields zero tax', () => {
    expect(roundHalfUpBps(999, 0)).toBe(0);
  });

  test('invalid input renders as 0 instead of crashing', () => {
    expect(roundHalfUpBps(undefined, 500)).toBe(0);
    expect(roundHalfUpBps(100, NaN)).toBe(0);
  });
});

describe('percentToBps', () => {
  test('parses plain and percent-suffixed rates', () => {
    expect(percentToBps('8.25')).toBe(825);
    expect(percentToBps('8.25%')).toBe(825);
    expect(percentToBps('2')).toBe(200);
    expect(percentToBps('0')).toBe(0);
  });

  test('rejects non-percentages', () => {
    expect(percentToBps('')).toBeNull();
    expect(percentToBps('abc')).toBeNull();
    expect(percentToBps('-5')).toBeNull();
    expect(percentToBps(null)).toBeNull();
  });
});

describe('bpsToPercent', () => {
  test('formats basis points as a percent string', () => {
    expect(bpsToPercent(825)).toBe('8.25');
    expect(bpsToPercent(0)).toBe('0');
    expect(bpsToPercent(200)).toBe('2');
  });

  test('invalid input renders as 0 instead of crashing', () => {
    expect(bpsToPercent(undefined)).toBe('0');
    expect(bpsToPercent(NaN)).toBe('0');
  });
});
