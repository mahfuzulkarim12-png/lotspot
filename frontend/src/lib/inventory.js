// Pure inventory-list transforms shared by the customer view and admin panels.

const byName = (a, b) =>
  a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });

/** Case-insensitive match on product name or SKU. Empty query keeps all. */
export function filterProducts(products, query) {
  const q = query.trim().toLowerCase();
  if (!q) return products;
  return products.filter(
    (p) => p.name.toLowerCase().includes(q) || p.sku.toLowerCase().includes(q)
  );
}

/**
 * Apply one SSE inventory event to the current product list, immutably.
 * Events: {action: 'created'|'updated', product} | {action: 'deleted', product_id}
 */
export function applyInventoryEvent(products, event) {
  if (!Array.isArray(products)) return products;
  switch (event.action) {
    case 'created':
    case 'updated': {
      const rest = products.filter((p) => p.id !== event.product.id);
      return [...rest, event.product].sort(byName);
    }
    case 'deleted':
      return products.filter((p) => p.id !== event.product_id);
    default:
      return products;
  }
}

/** Stock status for badges: 'out' | 'low' | 'ok' (low = 5 or fewer left). */
export const LOW_STOCK_THRESHOLD = 5;

export function stockStatus(qty) {
  if (qty <= 0) return 'out';
  if (qty <= LOW_STOCK_THRESHOLD) return 'low';
  return 'ok';
}
