import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { formatCents } from '../lib/money';

function todayISO() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

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

export default function DailyHistoryPanel() {
  const [range, setRange] = useState(() => defaultRange(7));
  const [history, setHistory] = useState(null);
  const [error, setError] = useState(null);
  const [preset, setPreset] = useState('7');

  const loadRange = useCallback(async (nextRange) => {
    setError(null);
    try {
      const data = await api.salesHistory(nextRange.start, nextRange.end);
      setHistory(data);
    } catch (err) {
      setError(err.message);
      setHistory(null);
    }
  }, []);

  useEffect(() => {
    loadRange(range);
  }, [range, loadRange]);

  const setPresetRange = (value) => {
    setPreset(value);
    if (value === 'custom') return;
    const days = Number(value);
    setRange(defaultRange(days));
  };

  const updateRange = (field) => (e) => {
    setPreset('custom');
    setRange((prev) => ({ ...prev, [field]: e.target.value }));
  };

  const days = history?.days ?? [];

  return (
    <section aria-labelledby="history-heading" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      <div>
        <h1 className="panel-title" id="history-heading">Daily sales history</h1>
        <p className="panel-sub">
          Review sales totals across a range of business days.
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
          <div className="field">
            <span>Days</span>
            <strong className="num" style={{ padding: 'var(--space-2) 0' }}>
              {days.length}
            </strong>
          </div>
        </div>
      </div>

      {error && <p className="form-error" role="alert">{error}</p>}

      <div className="table-card">
        <table className="data-table">
          <thead>
            <tr>
              <th>Date</th>
              <th className="col-num">Revenue</th>
              <th className="col-num">Items sold</th>
              <th className="col-num">Transactions</th>
            </tr>
          </thead>
          <tbody>
            {days.map((day) => (
              <tr key={day.date}>
                <td className="num">{day.date}</td>
                <td className="col-num num">{formatCents(day.total_revenue_cents)}</td>
                <td className="col-num num">{day.total_items_sold}</td>
                <td className="col-num num">{day.transaction_count}</td>
              </tr>
            ))}
            {history && days.length === 0 && (
              <tr>
                <td colSpan={4} style={{ textAlign: 'center', color: 'var(--ink-muted)' }}>
                  No sales found for this range.
                </td>
              </tr>
            )}
            {!history && !error && (
              <tr>
                <td colSpan={4} style={{ textAlign: 'center', color: 'var(--ink-muted)' }}>
                  Loading history…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
