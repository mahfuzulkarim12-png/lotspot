// All API money values are integer cents; these are the only two conversions.

const BPS_DENOMINATOR = 10000; // basis points; 10000 bps == 100%

/**
 * Round-half-up of amountCents * rateBps / 10000, integer cents only.
 * Mirrors the backend's `_round_half_up_bps` exactly, so a client-side tax
 * estimate always matches what the server will charge.
 */
export function roundHalfUpBps(amountCents, rateBps) {
  const safeAmount = Number.isInteger(amountCents) ? amountCents : 0;
  const safeRate = Number.isInteger(rateBps) ? rateBps : 0;
  return Math.floor((safeAmount * safeRate + BPS_DENOMINATOR / 2) / BPS_DENOMINATOR);
}

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

/**
 * Parse operator input like "8.25" or "8.25%" into integer basis points.
 * Returns null for anything that isn't a non-negative percentage.
 */
export function percentToBps(input) {
  if (typeof input !== 'string' && typeof input !== 'number') return null;
  const cleaned = String(input).trim().replace(/%$/, '');
  if (cleaned === '' || !/^\d*\.?\d*$/.test(cleaned) || cleaned === '.') return null;
  const value = Number.parseFloat(cleaned);
  if (!Number.isFinite(value) || value < 0) return null;
  return Math.round(value * 100);
}

/** 825 -> "8.25" (basis points to a percent string for form display). */
export function bpsToPercent(bps) {
  const safe = Number.isInteger(bps) ? bps : 0;
  return (safe / 100).toString();
}
