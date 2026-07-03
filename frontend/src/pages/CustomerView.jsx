import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useInventory } from '../hooks/useInventory';
import { filterProducts } from '../lib/inventory';
import ProductGrid from '../components/ProductGrid';

export default function CustomerView() {
  const { products, connected, error } = useInventory();
  const [query, setQuery] = useState('');

  // Customers only see what is actually on the shelf.
  const inStock = (products ?? []).filter((p) => p.qty > 0);
  const visible = filterProducts(inStock, query);

  return (
    <>
      <header className="topbar">
        <Link to="/" className="brand">
          Lot<em>Spot</em>
        </Link>
        <span className="topbar-note">What&rsquo;s on the shelf right now</span>
        <span className="topbar-spacer" />
        <span className={`live-dot${connected ? ' is-live' : ''}`}>
          {connected ? 'Live' : 'Reconnecting'}
        </span>
      </header>

      <main>
        <section className="customer-hero" aria-labelledby="hero-heading">
          <h1 id="hero-heading">
            In stock. <em>Right now.</em>
          </h1>
          <p>
            Live counts straight from the register — if it&rsquo;s listed here,
            it&rsquo;s on the shelf.
          </p>
        </section>

        <div className="search-row">
          <input
            type="search"
            className="search-input"
            placeholder="Search by name or SKU…"
            aria-label="Search products"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        {error && <p className="page-status">Couldn&rsquo;t load inventory: {error}</p>}
        {!error && products === null && <p className="page-status">Loading the shelf…</p>}
        {!error && products !== null && (
          <ProductGrid
            products={visible}
            emptyNote={
              query
                ? `Nothing in stock matches “${query}”.`
                : 'The shelf is empty right now — check back soon.'
            }
          />
        )}
      </main>
    </>
  );
}
