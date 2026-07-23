import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { todayISO } from '../lib/date';
import { formatCents } from '../lib/money';
import { useSaleEvents } from '../hooks/useSaleEvents';

export default function SummaryPanel() {
  const [day, setDay] = useState(todayISO);
  const [summary, setSummary] = useState(null);
  const [sales, setSales] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async (targetDay) => {
    setError(null);
    try {
      const [summaryData, salesData] = await Promise.all([
        api.salesSummary(targetDay),
        api.listSales(targetDay),
      ]);
      setSummary(summaryData);
      setSales(salesData);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    load(day);
  }, [day, load]);

  const salesConnected = useSaleEvents(() => {
    load(day);
  });

  const maxQty = summary?.top_items?.[0]?.qty_sold ?? 0;

  return (
    <section aria-labelledby="summary-heading" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      <div>
        <h1 className="panel-title" id="summary-heading">Daily summary</h1>
        <p className="panel-sub">
          Sales feed {salesConnected ? 'live' : 'reconnecting'}.
        </p>
        <div className="summary-controls" style={{ marginTop: 'var(--space-3)' }}>
          <label className="field">
            <span>Business day</span>
            <input
              className="input"
              type="date"
              value={day}
              onChange={(e) => e.target.value && setDay(e.target.value)}
            />
          </label>
          <button className="btn" type="button" onClick={() => load(day)}>
            Refresh
          </button>
          {day !== todayISO() && (
            <button className="btn btn-ghost" type="button" onClick={() => setDay(todayISO())}>
              Jump to today
            </button>
          )}
        </div>
      </div>

      {error && <p className="form-error" role="alert">{error}</p>}

      {summary && (
        <div className="stat-row">
          <div className="stat-tile">
            <p className="stat-label">Net revenue</p>
            <p className="stat-value num">{formatCents(summary.total_revenue_cents)}</p>
            <p className="stat-hint">{day}, tax excluded</p>
          </div>
          <div className="stat-tile">
            <p className="stat-label">Tax collected</p>
            <p className="stat-value num">{formatCents(summary.total_tax_cents)}</p>
            <p className="stat-hint">across all jurisdictions</p>
          </div>
          <div className="stat-tile">
            <p className="stat-label">Items sold</p>
            <p className="stat-value num">{summary.total_items_sold}</p>
            <p className="stat-hint">across all products</p>
          </div>
          <div className="stat-tile">
            <p className="stat-label">Transactions</p>
            <p className="stat-value num">{summary.transaction_count}</p>
            <p className="stat-hint">manual + POS</p>
          </div>
        </div>
      )}

      {summary && summary.top_items.length > 0 && (
        <div className="table-card">
          <div className="top-items" aria-label="Top selling items">
            {summary.top_items.map((item) => (
              <div key={item.sku} className="top-item">
                <span className="top-item-name">
                  {item.name} <span className="product-sku">{item.sku}</span>
                </span>
                <span className="top-item-track" aria-hidden="true">
                  <span
                    className="top-item-fill"
                    style={{ width: `${maxQty ? (item.qty_sold / maxQty) * 100 : 0}%` }}
                  />
                </span>
                <span className="top-item-meta num">
                  {item.qty_sold} sold · {formatCents(item.revenue_cents)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="table-card">
        <table className="data-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Product</th>
              <th>SKU</th>
              <th className="col-num">Qty</th>
              <th className="col-num">Unit</th>
              <th className="col-num">Total</th>
              <th className="col-num">Tax</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {(sales ?? []).map((sale) => (
              <tr key={sale.id}>
                <td className="num">{sale.sold_at.slice(11, 16)}</td>
                <td>{sale.product_name}</td>
                <td className="num">{sale.sku}</td>
                <td className="col-num num">{sale.qty}</td>
                <td className="col-num num">{formatCents(sale.unit_price_cents)}</td>
                <td className="col-num num">{formatCents(sale.total_cents)}</td>
                <td className="col-num num">{formatCents(sale.tax_cents)}</td>
                <td>{sale.source}</td>
              </tr>
            ))}
            {sales !== null && sales.length === 0 && (
              <tr>
                <td colSpan={8} style={{ textAlign: 'center', color: 'var(--ink-muted)' }}>
                  No sales recorded for {day}.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
