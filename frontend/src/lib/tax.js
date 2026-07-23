// Client-side mirror of the backend's per-jurisdiction tax computation
// (see backend/app.py:_compute_line_tax), used only for a live cart
// preview. The checkout response is always the source of truth.

import { roundHalfUpBps } from './money';

function isAccountActive(account, asOfDay) {
  if (account.effective_from > asOfDay) return false;
  if (account.effective_to && account.effective_to < asOfDay) return false;
  return true;
}

/** Active tax_accounts mapped to a tax_category as of a YYYY-MM-DD day. */
export function activeAccountsForCategory(categoryId, taxCategories, taxAccountsById, asOfDay) {
  if (categoryId == null) return [];
  const category = taxCategories.find((c) => c.id === categoryId);
  if (!category) return [];
  return category.tax_account_ids
    .map((id) => taxAccountsById.get(id))
    .filter(Boolean)
    .filter((account) => isAccountActive(account, asOfDay));
}

/** Per-jurisdiction tax lines for one cart line's total, integer cents only. */
export function computeLineTax(lineTotalCents, activeAccounts) {
  return activeAccounts.map((account) => ({
    tax_account_id: account.id,
    tax_account_name: account.name,
    rate_bps: account.rate_bps,
    tax_cents: roundHalfUpBps(lineTotalCents, account.rate_bps),
  }));
}

/**
 * Tax for a full cart. `lines` is `[{tax_category_id, line_total_cents}]`.
 * Returns the total tax in cents plus a per-jurisdiction breakdown summed
 * across all lines.
 */
export function computeCartTax(lines, taxCategories, taxAccounts, asOfDay) {
  const taxAccountsById = new Map(taxAccounts.map((account) => [account.id, account]));
  const breakdown = new Map();
  let taxCents = 0;

  for (const line of lines) {
    const accounts = activeAccountsForCategory(
      line.tax_category_id,
      taxCategories,
      taxAccountsById,
      asOfDay
    );
    for (const lineTax of computeLineTax(line.line_total_cents, accounts)) {
      taxCents += lineTax.tax_cents;
      const existing = breakdown.get(lineTax.tax_account_id);
      if (existing) {
        existing.tax_cents += lineTax.tax_cents;
      } else {
        breakdown.set(lineTax.tax_account_id, { ...lineTax });
      }
    }
  }

  return {
    taxCents,
    breakdown: [...breakdown.values()].sort((a, b) =>
      a.tax_account_name.localeCompare(b.tax_account_name)
    ),
  };
}
