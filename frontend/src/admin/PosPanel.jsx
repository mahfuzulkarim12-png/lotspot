import { useEffect, useMemo, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { api } from '../api/client';
import { formatCents } from '../lib/money';
import { filterProducts, stockStatus } from '../lib/inventory';

const PAYMENT_METHODS = [
  { value: 'cash', label: 'Cash' },
  { value: 'card', label: 'Card' },
  { value: 'mixed', label: 'Mixed' },
];

const QUICK_PICK_LIMIT = 8;

function sortProducts(a, b) {
  return b.qty - a.qty || a.name.localeCompare(b.name);
}

function cartList(cart, products) {
  return Object.values(cart)
    .map((line) => {
      const product = products.find((item) => item.id === line.product_id);
      if (!product) return null;
      return {
        ...line,
        product,
        line_total_cents: line.qty * product.price_cents,
      };
    })
    .filter(Boolean)
    .sort((a, b) => a.product.name.localeCompare(b.product.name));
}

export default function PosPanel() {
  const { products, connected } = useOutletContext();
  const [query, setQuery] = useState('');
  const [cart, setCart] = useState({});
  const [paymentMethod, setPaymentMethod] = useState('cash');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [receipt, setReceipt] = useState(null);
  const loading = products === null;

  const inStock = useMemo(
    () => (products ?? []).filter((product) => product.qty > 0),
    [products]
  );
  const visibleProducts = useMemo(
    () => filterProducts(inStock, query).slice().sort(sortProducts),
    [inStock, query]
  );
  const quickPick = useMemo(
    () => inStock.slice().sort(sortProducts).slice(0, QUICK_PICK_LIMIT),
    [inStock]
  );
  const cartLines = useMemo(
    () => cartList(cart, products ?? []),
    [cart, products]
  );
  const totalQty = cartLines.reduce((sum, line) => sum + line.qty, 0);
  const totalCents = cartLines.reduce((sum, line) => sum + line.line_total_cents, 0);

  useEffect(() => {
    if (!products) return;

    let adjusted = false;
    const nextCart = {};
    for (const line of Object.values(cart)) {
      const product = products.find((item) => item.id === line.product_id);
      if (!product || product.qty <= 0) {
        adjusted = true;
        continue;
      }
      const qty = Math.min(line.qty, product.qty);
      if (qty !== line.qty) adjusted = true;
      nextCart[product.id] = { product_id: product.id, qty };
    }

    if (adjusted) {
      setNotice('Cart adjusted to match live stock.');
      setCart(nextCart);
    }
  }, [cart, products]);

  const addProduct = (product, qty = 1) => {
    setReceipt(null);
    setError(null);
    setNotice(null);
    setCart((prev) => {
      const current = prev[product.id]?.qty ?? 0;
      const nextQty = Math.min(product.qty, current + qty);
      if (nextQty === current) return prev;
      return {
        ...prev,
        [product.id]: { product_id: product.id, qty: nextQty },
      };
    });
  };

  const updateQty = (product, nextValue) => {
    const qty = Number.parseInt(nextValue, 10);
    setReceipt(null);
    setError(null);
    setNotice(null);
    setCart((prev) => {
      if (!Number.isInteger(qty) || qty <= 0) {
        const next = { ...prev };
        delete next[product.id];
        return next;
      }
      const nextQty = Math.min(product.qty, qty);
      return {
        ...prev,
        [product.id]: { product_id: product.id, qty: nextQty },
      };
    });
  };

  const removeLine = (productId) => {
    setReceipt(null);
    setError(null);
    setNotice(null);
    setCart((prev) => {
      const next = { ...prev };
      delete next[productId];
      return next;
    });
  };

  const clearCart = () => {
    setCart({});
    setReceipt(null);
    setError(null);
    setNotice(null);
  };

  const checkout = async (e) => {
    e.preventDefault();
    if (cartLines.length === 0) {
      setError('Add at least one item to the cart.');
      return;
    }

    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const checkoutPayload = {
        payment_method: paymentMethod,
        items: cartLines.map((line) => ({
          product_id: line.product_id,
          qty: line.qty,
        })),
      };
      const result = await api.posCheckout(checkoutPayload);
      setReceipt(result);
      setCart({});
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const searchEmptyNote =
    query.trim() === ''
      ? 'No products are currently in stock.'
      : `No live stock matches “${query}”.`;

  return (
    <section className="pos-layout" aria-labelledby="pos-heading">
      <div className="pos-hero">
        <div>
          <h1 className="panel-title" id="pos-heading">Point of sale</h1>
          <p className="panel-sub">
            Search live stock, build a cart, and complete a checkout in one step.
          </p>
        </div>
        <span className={`live-dot${connected ? ' is-live' : ''}`}>
          {connected ? 'Live inventory' : 'Reconnecting'}
        </span>
      </div>

      <div className="pos-grid">
        <div className="pos-main">
          <label className="field pos-search">
            <span>Search products</span>
            <input
              className="input pos-search-input"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search SKU or product name…"
            />
          </label>

          <div className="pos-section">
            <div className="section-head">
              <div>
                <h2 className="section-title">Quick select</h2>
                <p className="section-sub">Fast access to live, in-stock items.</p>
              </div>
            </div>
            <div className="pos-quick-grid">
              {loading && <p className="empty-note">Loading live stock…</p>}
              {!loading && quickPick.map((product) => {
                const status = stockStatus(product.qty);
                const inCart = cart[product.id]?.qty ?? 0;
                const atLimit = inCart >= product.qty;
                return (
                  <button
                    key={product.id}
                    className="pos-quick-card"
                    type="button"
                    onClick={() => addProduct(product, 1)}
                    disabled={atLimit}
                  >
                    <div className="pos-quick-card-head">
                      <strong>{product.name}</strong>
                      <span className={`badge badge-${status}`}>
                        {status === 'ok' ? 'In stock' : status === 'low' ? 'Low' : 'Out'}
                      </span>
                    </div>
                    <span className="product-sku">{product.sku}</span>
                    <div className="pos-quick-card-foot">
                      <span className="num">{formatCents(product.price_cents)}</span>
                      <span className="num">{product.qty} left</span>
                    </div>
                    </button>
                  );
              })}
            </div>
          </div>

          <div className="pos-section">
            <div className="section-head">
              <div>
                <h2 className="section-title">Search results</h2>
                <p className="section-sub">
                  {loading
                    ? 'Loading live stock…'
                    : `${visibleProducts.length} live products found.`}
                </p>
              </div>
            </div>
            <div className="pos-product-grid">
              {loading && <p className="empty-note">Loading live stock…</p>}
              {!loading && visibleProducts.map((product) => {
                const status = stockStatus(product.qty);
                const inCart = cart[product.id]?.qty ?? 0;
                const canAdd = inCart < product.qty;
                return (
                  <article key={product.id} className="pos-product-card">
                    <div className="pos-product-card-head">
                      <div>
                        <h3>{product.name}</h3>
                        <span className="product-sku">{product.sku}</span>
                      </div>
                      <span className={`badge badge-${status}`}>
                        {status === 'ok' ? 'In stock' : status === 'low' ? 'Low stock' : 'Out'}
                      </span>
                    </div>
                    <div className="pos-product-card-foot">
                      <span className="product-price num">{formatCents(product.price_cents)}</span>
                      <span className="num">{product.qty} available</span>
                    </div>
                    <button
                      className="btn btn-primary"
                      type="button"
                      onClick={() => addProduct(product, 1)}
                      disabled={!canAdd}
                    >
                      {canAdd ? (inCart > 0 ? `Add another (${inCart} in cart)` : 'Add to cart') : 'Limit reached'}
                    </button>
                  </article>
                );
              })}
              {!loading && visibleProducts.length === 0 && (
                <p className="empty-note">{searchEmptyNote}</p>
              )}
            </div>
          </div>
        </div>

        <aside className="pos-cart" aria-labelledby="cart-heading">
          <div className="section-head">
            <div>
              <h2 className="section-title" id="cart-heading">Cart</h2>
              <p className="section-sub">
                {totalQty === 0 ? 'Cart is empty.' : `${totalQty} item${totalQty === 1 ? '' : 's'} ready.`}
              </p>
            </div>
            <button className="btn btn-ghost" type="button" onClick={clearCart} disabled={cartLines.length === 0}>
              Clear
            </button>
          </div>

          <div className="pos-cart-lines">
            {cartLines.map((line) => (
              <article key={line.product_id} className="pos-cart-line">
                <div className="pos-cart-line-main">
                  <strong>{line.product.name}</strong>
                  <span className="product-sku">{line.product.sku}</span>
                  <span className="num">{formatCents(line.product.price_cents)} each</span>
                </div>
                <div className="pos-cart-line-controls">
                  <label className="field">
                    <span>Qty</span>
                    <input
                      className="input inline-input pos-qty-input"
                      inputMode="numeric"
                      type="number"
                      min="1"
                      max={line.product.qty}
                      value={line.qty}
                      onChange={(e) => updateQty(line.product, e.target.value)}
                    />
                  </label>
                  <strong className="num">{formatCents(line.line_total_cents)}</strong>
                  <button className="btn btn-danger" type="button" onClick={() => removeLine(line.product_id)}>
                    Remove
                  </button>
                </div>
              </article>
            ))}
            {cartLines.length === 0 && (
              <p className="empty-note">Add products from the search results or quick-select grid.</p>
            )}
          </div>

          <form className="pos-checkout" onSubmit={checkout}>
            <label className="field">
              <span>Payment method</span>
              <select
                className="input"
                value={paymentMethod}
                onChange={(e) => setPaymentMethod(e.target.value)}
              >
                {PAYMENT_METHODS.map((method) => (
                  <option key={method.value} value={method.value}>
                    {method.label}
                  </option>
                ))}
              </select>
            </label>

            <div className="pos-total">
              <div>
                <p className="stat-label">Running total</p>
                <p className="stat-value num">{formatCents(totalCents)}</p>
              </div>
              <div className="pos-total-meta">
                <span className="stat-hint">{cartLines.length} line{cartLines.length === 1 ? '' : 's'}</span>
                <span className="stat-hint">{totalQty} item{totalQty === 1 ? '' : 's'}</span>
              </div>
            </div>

            {notice && <p className="form-success" role="status">{notice}</p>}
            {error && <p className="form-error" role="alert">{error}</p>}

            <button className="btn btn-primary pos-checkout-btn" type="submit" disabled={busy || cartLines.length === 0}>
              {busy ? 'Completing checkout…' : 'Complete checkout'}
            </button>
          </form>

          {receipt && (
            <div className="pos-receipt" role="status">
              <p className="section-title">Checkout complete</p>
              <p className="section-sub">
                Transaction {receipt.transaction_id.slice(0, 8)} by {receipt.cashier} using {receipt.payment_method}.
              </p>
              <p className="pos-receipt-total num">
                {receipt.item_count} line{receipt.item_count === 1 ? '' : 's'} · {receipt.total_qty} item{receipt.total_qty === 1 ? '' : 's'} · {formatCents(receipt.total_cents)}
              </p>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
