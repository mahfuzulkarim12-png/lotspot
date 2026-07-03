import { useMemo, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { api } from '../api/client';
import { formatCents } from '../lib/money';
import { filterProducts } from '../lib/inventory';

export default function SalesEntryPanel() {
  const { products } = useOutletContext();
  const [productId, setProductId] = useState('');
  const [qty, setQty] = useState('1');
  const [filter, setFilter] = useState('');
  const [error, setError] = useState(null);
  const [lastSale, setLastSale] = useState(null);
  const [busy, setBusy] = useState(false);

  const sellable = useMemo(
    () => filterProducts(products ?? [], filter).filter((p) => p.qty > 0),
    [products, filter]
  );
  const selected = (products ?? []).find((p) => p.id === Number(productId));
  const qtyNumber = Number(qty);
  const qtyValid = Number.isInteger(qtyNumber) && qtyNumber > 0;
  const totalCents = selected && qtyValid ? selected.price_cents * qtyNumber : null;

  const submit = async (e) => {
    e.preventDefault();
    if (!selected) {
      setError('Pick a product first');
      return;
    }
    if (!qtyValid) {
      setError('Qty must be a whole number greater than zero');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const sale = await api.createSale({ product_id: selected.id, qty: qtyNumber });
      setLastSale(sale);
      setQty('1');
    } catch (err) {
      setError(err.message);
      setLastSale(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="sale-heading">
      <h1 className="panel-title" id="sale-heading">Log a sale</h1>
      <p className="panel-sub">
        For walk-up sales rung outside the POS. Stock is deducted immediately.
      </p>

      <form className="form-card" onSubmit={submit} style={{ marginTop: 'var(--space-4)' }}>
        <label className="field" style={{ minWidth: 180 }}>
          <span>Find product</span>
          <input
            className="input"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Type to narrow the list…"
          />
        </label>

        <label className="field" style={{ flex: 1, minWidth: 220 }}>
          <span>Product</span>
          <select
            className="input"
            value={productId}
            onChange={(e) => setProductId(e.target.value)}
          >
            <option value="">— choose —</option>
            {sellable.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} · {formatCents(p.price_cents)} · {p.qty} left
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>Qty</span>
          <input
            className="input inline-input"
            inputMode="numeric"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
          />
        </label>

        <div className="field">
          <span>Total</span>
          <strong className="num" style={{ fontSize: 'var(--text-lg)', padding: 'var(--space-1) 0' }}>
            {totalCents === null ? '—' : formatCents(totalCents)}
          </strong>
        </div>

        <button className="btn btn-primary" type="submit" disabled={busy || !selected}>
          {busy ? 'Recording…' : 'Record sale'}
        </button>
      </form>

      {error && (
        <p className="form-error" role="alert" style={{ marginTop: 'var(--space-3)' }}>
          {error}
        </p>
      )}
      {lastSale && !error && (
        <p className="form-success" role="status" style={{ marginTop: 'var(--space-3)' }}>
          Recorded: {lastSale.qty} × {lastSale.product_name} for{' '}
          {formatCents(lastSale.total_cents)} at {lastSale.sold_at.replace('T', ' ')}.
        </p>
      )}
    </section>
  );
}
