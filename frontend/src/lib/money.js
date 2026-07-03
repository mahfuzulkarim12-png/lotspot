// All API money values are integer cents; these are the only two conversions.

/** 450 -> "$4.50" (grouped thousands). Invalid input renders as "$0.00". */
export function formatCents(cents) {
  const safe = Number.isFinite(cents) ? cents : 0;
  const dollars = safe / 100;
  return `$${dollars.toLocaleString('en', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/**
 * Parse operator input like "4.50", "$4.50", "1,234.5" into integer cents.
 * Returns null for anything that isn't a non-negative money amount.
 */
export function dollarsToCents(input) {
  if (typeof input !== 'string' && typeof input !== 'number') return null;
  const cleaned = String(input).trim().replace(/^\$/, '').replace(/,/g, '');
  if (cleaned === '' || !/^\d*\.?\d*$/.test(cleaned) || cleaned === '.') return null;
  const value = Number.parseFloat(cleaned);
  if (!Number.isFinite(value) || value < 0) return null;
  return Math.round(value * 100);
}
