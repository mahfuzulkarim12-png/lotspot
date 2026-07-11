import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { formatDuration } from '../lib/duration';
import { useTimeclockEvents } from '../hooks/useTimeclockEvents';

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

function formatDateTime(iso) {
  if (!iso) return 'In progress';
  return iso.replace('T', ' ').slice(0, 16);
}

export default function TimeclockHistoryPanel() {
  const [employees, setEmployees] = useState([]);
  const [employeeFilter, setEmployeeFilter] = useState('');
  const [range, setRange] = useState(() => defaultRange(7));
  const [preset, setPreset] = useState('7');
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.listEmployees().then(setEmployees).catch(() => setEmployees([]));
  }, []);

  const load = useCallback(async (nextRange, nextEmployeeFilter) => {
    setError(null);
    try {
      const data = await api.timeclockHistory({
        employeeId: nextEmployeeFilter || undefined,
        start: nextRange.start,
        end: nextRange.end,
      });
      setEntries(data.entries);
    } catch (err) {
      setError(err.message);
      setEntries(null);
    }
  }, []);

  useEffect(() => {
    load(range, employeeFilter);
  }, [range, employeeFilter, load]);

  const connected = useTimeclockEvents(() => {
    load(range, employeeFilter);
  });

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

  const rows = entries ?? [];

  return (
    <section aria-labelledby="timeclock-history-heading" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      <div>
        <h1 className="panel-title" id="timeclock-history-heading">Shift history</h1>
        <p className="panel-sub">Review clock-in/out shifts per employee.</p>
        <p className="panel-sub" style={{ marginTop: 'var(--space-1)' }}>
          Timeclock feed {connected ? 'live' : 'reconnecting'}.
        </p>
        <div className="summary-controls" style={{ marginTop: 'var(--space-3)' }}>
          <label className="field">
            <span>Employee</span>
            <select
              className="input"
              value={employeeFilter}
              onChange={(e) => setEmployeeFilter(e.target.value)}
            >
              <option value="">All employees</option>
              {employees.map((employee) => (
                <option key={employee.id} value={employee.id}>
                  {employee.name}
                </option>
              ))}
            </select>
          </label>
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
        </div>
      </div>

      {error && <p className="form-error" role="alert">{error}</p>}

      <div className="table-card">
        <table className="data-table">
          <thead>
            <tr>
              <th>Employee</th>
              <th>Clock in</th>
              <th>Clock out</th>
              <th className="col-num">Duration</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((entry) => (
              <tr key={entry.id}>
                <td>{entry.employee_name}</td>
                <td className="num">{formatDateTime(entry.clock_in_at)}</td>
                <td className="num">{formatDateTime(entry.clock_out_at)}</td>
                <td className="col-num num">
                  {entry.duration_minutes === null ? '—' : formatDuration(entry.duration_minutes)}
                </td>
              </tr>
            ))}
            {entries && rows.length === 0 && (
              <tr>
                <td colSpan={4} style={{ textAlign: 'center', color: 'var(--ink-muted)' }}>
                  No shifts found for this range.
                </td>
              </tr>
            )}
            {!entries && !error && (
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
