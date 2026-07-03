import { formatCents } from '../lib/money';
import { stockStatus } from '../lib/inventory';

const BADGE_LABEL = { ok: 'In stock', low: 'Low stock', out: 'Out' };

export default function ProductGrid({ products, emptyNote }) {
  return (
    <section className="product-grid" aria-label="Products in stock">
      {products.length === 0 && <p className="empty-note">{emptyNote}</p>}
      {products.map((product) => {
        const status = stockStatus(product.qty);
        return (
          <article key={product.id} className="product-card">
            <div className="product-card-head">
              <h3>{product.name}</h3>
              <span className={`badge badge-${status}`}>{BADGE_LABEL[status]}</span>
            </div>
            <span className="product-sku">{product.sku}</span>
            <div className="product-card-foot">
              <span className="product-price num">{formatCents(product.price_cents)}</span>
              <span className="product-qty num">{product.qty} left</span>
            </div>
          </article>
        );
      })}
    </section>
  );
}
