import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { todayISO } from '../lib/date';
import { formatCents } from '../lib/money';
import { useSaleEvents } from '../hooks/useSaleEvents';

function shiftISO(days) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function defaultRange(days) {
  return {
    start: shiftISO(-(days - 1)),
    end: todayISO(),
  };
}

function formatDateTime(iso) {
  if (!iso) return '—';
  return iso.replace('T', ' ').slice(0, 16);
}

function shortReceiptId(transactionId) {
  return transactionId ? transactionId.slice(0, 8) : '—';
}

export default function VoidedPanel() {
  const [range, setRange] = useState(() => defaultRange(7));
  const [preset, setPreset] = useState('7');
  const [query, setQuery] = useState('');
  const [receipts, setReceipts] = useState(null);
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(() => new Set());

  const load = useCallback(async (nextRange, nextQuery) => {
    setError(null);
    try {
      const filters = { start: nextRange.start, end: nextRange.end, q: nextQuery || undefined };
      const [receiptData, itemData] = await Promise.all([
        api.voidedReceipts(filters),
        api.voidedItems(filters),
      ]);
      setReceipts(receiptData);
      setItems(itemData);
    } catch (err) {
      setError(err.message);
      setReceipts(null);
      setItems(null);
    }
  }, []);

  useEffect(() => {
    load(range, query);
  }, [range, query, load]);

  const connected = useSaleEvents(() => {
    load(range, query);
  });

  const setPresetRange = (value) => {
    setPreset(value);
    if (value === 'custom') return;
    setRange(defaultRange(Number(value)));
  };

  const updateRange = (field) => (e) => {
    setPreset('custom');
    setRange((prev) => ({ ...prev, [field]: e.target.value }));
  };

  const toggleExpanded = (transactionId) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(transactionId)) {
        next.delete(transactionId);
      } else {
        next.add(transactionId);
      }
      return next;
    });
  };

  const receiptRows = receipts ?? [];
  const itemRows = items ?? [];
  const receiptColumnCount = 8;
  const itemColumnCount = 7;

  const totalVoidedReceiptCents = useMemo(
    () => receiptRows.reduce((sum, receipt) => sum + receipt.grand_total_cents, 0),
    [receiptRows]
  );

  return (
    <section aria-labelledby="voided-heading" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      <div>
        <h1 className="panel-title" id="voided-heading">Voided sales</h1>
        <p className="panel-sub">
          Review fully voided receipts and individually voided cart items.
        </p>
        <p className="panel-sub" style={{ marginTop: 'var(--space-1)' }}>
          Sales feed {connected ? 'live' : 'reconnecting'}.
        </p>
        <div className="summary-controls" style={{ marginTop: 'var(--space-3)' }}>
          <label className="field">
            <span>Range</span>
            <select className="input" value={preset} onChange={(e) => setPresetRange(e.target.value)}>
              <option value="7">Last 7 days</option>
              <option value="30">Last 30 days</option>
              <option value="custom">Custom</option>
            </select>
          </label>
          <label className="field">
            <span>Start</span>
            <input className="input" type="date" value={range.start} onChange={updateRange('start')} />
          </label>
          <label className="field">
            <span>End</span>
            <input className="input" type="date" value={range.end} onChange={updateRange('end')} />
          </label>
          <label className="field">
            <span>Receipt ID</span>
            <input
              className="input"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search receipt id…"
            />
          </label>
        </div>
      </div>

      {error && <p className="form-error" role="alert">{error}</p>}

      <div>
        <div className="section-head">
          <div>
            <h2 className="section-title">Voided receipts</h2>
            <p className="section-sub">
              {receipts === null
                ? 'Loading…'
                : `${receiptRows.length} receipt${receiptRows.length === 1 ? '' : 's'} · ${formatCents(totalVoidedReceiptCents)} voided`}
            </p>
          </div>
        </div>
        <div className="table-card">
          <table className="data-table">
            <thead>
              <tr>
                <th></th>
                <th>Date</th>
                <th>Receipt</th>
                <th>Cashier</th>
                <th>Payment</th>
                <th className="col-num">Items</th>
                <th className="col-num">Total</th>
                <th>Voided at</th>
              </tr>
            </thead>
            <tbody>
              {receiptRows.map((receipt) => {
                const isOpen = expanded.has(receipt.transaction_id);
                return (
                  <Fragment key={receipt.transaction_id}>
                    <tr>
                      <td>
                        <button
                          className="btn btn-ghost"
                          type="button"
                          onClick={() => toggleExpanded(receipt.transaction_id)}
                          aria-expanded={isOpen}
                          aria-label={isOpen ? 'Collapse line items' : 'Expand line items'}
                        >
                          {isOpen ? '−' : '+'}
                        </button>
                      </td>
                      <td className="num">{formatDateTime(receipt.sold_at)}</td>
                      <td className="num">{shortReceiptId(receipt.transaction_id)}</td>
                      <td>{receipt.cashier ?? '—'}</td>
                      <td>{receipt.payment_method ?? '—'}</td>
                      <td className="col-num num">{receipt.item_count}</td>
                      <td className="col-num num">{formatCents(receipt.grand_total_cents)}</td>
                      <td className="num">{formatDateTime(receipt.voided_at)}</td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td></td>
                        <td colSpan={receiptColumnCount - 1}>
                          <table className="data-table">
                            <thead>
                              <tr>
                                <th>Product</th>
                                <th>SKU</th>
                                <th className="col-num">Qty</th>
                                <th className="col-num">Unit</th>
                                <th className="col-num">Total</th>
                              </tr>
                            </thead>
                            <tbody>
                              {receipt.line_items.map((line) => (
                                <tr key={line.id}>
                                  <td>{line.product_name}</td>
                                  <td className="num">{line.sku}</td>
                                  <td className="col-num num">{line.qty}</td>
                                  <td className="col-num num">{formatCents(line.unit_price_cents)}</td>
                                  <td className="col-num num">{formatCents(line.total_cents)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
              {receipts !== null && receiptRows.length === 0 && (
                <tr>
                  <td colSpan={receiptColumnCount} style={{ textAlign: 'center', color: 'var(--ink-muted)' }}>
                    No voided receipts found for this range.
                  </td>
                </tr>
              )}
              {receipts === null && !error && (
                <tr>
                  <td colSpan={receiptColumnCount} style={{ textAlign: 'center', color: 'var(--ink-muted)' }}>
                    Loading voided receipts…
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <div className="section-head">
          <div>
            <h2 className="section-title">Voided items</h2>
            <p className="section-sub">
              {items === null
                ? 'Loading…'
                : `${itemRows.length} item${itemRows.length === 1 ? '' : 's'} voided from otherwise active receipts.`}
            </p>
          </div>
        </div>
        <div className="table-card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Voided at</th>
                <th>Receipt</th>
                <th>Product</th>
                <th>SKU</th>
                <th className="col-num">Qty</th>
                <th className="col-num">Price</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {itemRows.map((item) => (
                <tr key={item.id}>
                  <td className="num">{formatDateTime(item.voided_at)}</td>
                  <td className="num">{shortReceiptId(item.transaction_id)}</td>
                  <td>{item.product_name}</td>
                  <td className="num">{item.sku}</td>
                  <td className="col-num num">{item.qty}</td>
                  <td className="col-num num">{formatCents(item.unit_price_cents)}</td>
                  <td>{item.void_reason ?? '—'}</td>
                </tr>
              ))}
              {items !== null && itemRows.length === 0 && (
                <tr>
                  <td colSpan={itemColumnCount} style={{ textAlign: 'center', color: 'var(--ink-muted)' }}>
                    No voided items found for this range.
                  </td>
                </tr>
              )}
              {items === null && !error && (
                <tr>
                  <td colSpan={itemColumnCount} style={{ textAlign: 'center', color: 'var(--ink-muted)' }}>
                    Loading voided items…
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
