import { useEffect, useState } from 'react';
import { api } from '../api/client';

/**
 * One-time fetch of tax categories + tax accounts, used for a client-side
 * cart tax preview. The checkout response remains the source of truth;
 * if this fetch fails the cart just shows $0.00 tax until checkout.
 */
export function useTaxRates() {
  const [taxCategories, setTaxCategories] = useState([]);
  const [taxAccounts, setTaxAccounts] = useState([]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.listTaxCategories(), api.listTaxAccounts()])
      .then(([categories, accounts]) => {
        if (cancelled) return;
        setTaxCategories(categories);
        setTaxAccounts(accounts);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  return { taxCategories, taxAccounts };
}
