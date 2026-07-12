import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { useTimeclockEvents } from '../hooks/useTimeclockEvents';

const EMPTY_EMPLOYEE_DRAFT = { name: '', pin: '' };
const PIN_PATTERN = /^\d{4,8}$/;

function formatTime(iso) {
  return iso ? iso.slice(11, 16) : '';
}

export default function TimeclockPanel() {
  const [employees, setEmployees] = useState(null);
  const [openShifts, setOpenShifts] = useState(null);
  const [selectedEmployeeId, setSelectedEmployeeId] = useState('');
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const [employeeDraft, setEmployeeDraft] = useState(EMPTY_EMPLOYEE_DRAFT);
  const [employeeBusy, setEmployeeBusy] = useState(false);
  const [employeeError, setEmployeeError] = useState(null);

  const load = useCallback(async () => {
    try {
      const [employeeList, status] = await Promise.all([
        api.listEmployees(),
        api.timeclockStatus(),
      ]);
      setEmployees(employeeList);
      setOpenShifts(status.open_shifts);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const connected = useTimeclockEvents(() => {
    load();
  });

  const openShiftFor = (employeeId) =>
    (openShifts ?? []).find((shift) => shift.employee_id === employeeId) ?? null;

  const selectedShift = selectedEmployeeId
    ? openShiftFor(Number(selectedEmployeeId))
    : null;

  const submitClockAction = async (e) => {
    e.preventDefault();
    if (!selectedEmployeeId) {
      setError('Select an employee.');
      return;
    }
    if (!pin.trim()) {
      setError('Enter a PIN.');
      return;
    }

    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const employeeId = Number(selectedEmployeeId);
      const result = selectedShift
        ? await api.clockOut(employeeId, pin)
        : await api.clockIn(employeeId, pin);
      const action = selectedShift ? 'out' : 'in';
      const at = formatTime(result.clock_out_at ?? result.clock_in_at);
      setNotice(`${result.employee_name} clocked ${action} at ${at}.`);
      setPin('');
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const addEmployee = async (e) => {
    e.preventDefault();
    if (!employeeDraft.name.trim()) {
      setEmployeeError('Name is required.');
      return;
    }
    if (!PIN_PATTERN.test(employeeDraft.pin)) {
      setEmployeeError('PIN must be 4-8 digits.');
      return;
    }

    setEmployeeBusy(true);
    setEmployeeError(null);
    try {
      await api.createEmployee({
        name: employeeDraft.name.trim(),
        pin: employeeDraft.pin,
      });
      setEmployeeDraft(EMPTY_EMPLOYEE_DRAFT);
      await load();
    } catch (err) {
      setEmployeeError(err.message);
    } finally {
      setEmployeeBusy(false);
    }
  };

  const loading = employees === null || openShifts === null;

  return (
    <section aria-labelledby="timeclock-heading" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      <div>
        <h1 className="panel-title" id="timeclock-heading">Time clock</h1>
        <p className="panel-sub">Clock staff in and out and see who's on shift right now.</p>
        <p className="panel-sub" style={{ marginTop: 'var(--space-1)' }}>
          <span className={`live-dot${connected ? ' is-live' : ''}`}>
            {connected ? 'Live' : 'Reconnecting'}
          </span>
        </p>
      </div>

      <form className="form-card" onSubmit={submitClockAction}>
        <label className="field" style={{ flex: 1 }}>
          <span>Employee</span>
          <select
            className="input"
            value={selectedEmployeeId}
            onChange={(e) => {
              setSelectedEmployeeId(e.target.value);
              setError(null);
              setNotice(null);
            }}
          >
            <option value="">Select an employee…</option>
            {(employees ?? []).map((employee) => (
              <option key={employee.id} value={employee.id}>
                {employee.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>PIN</span>
          <input
            className="input inline-input"
            type="password"
            inputMode="numeric"
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            placeholder="••••"
          />
        </label>
        <button className="btn btn-primary" type="submit" disabled={busy || loading}>
          {busy ? 'Working…' : selectedShift ? 'Clock out' : 'Clock in'}
        </button>
      </form>

      {notice && <p className="form-success" role="status">{notice}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}

      <div className="table-card">
        <table className="data-table">
          <thead>
            <tr>
              <th>Employee</th>
              <th>Clocked in at</th>
            </tr>
          </thead>
          <tbody>
            {(openShifts ?? []).map((shift) => (
              <tr key={shift.id}>
                <td>{shift.employee_name}</td>
                <td className="num">{formatTime(shift.clock_in_at)}</td>
              </tr>
            ))}
            {!loading && (openShifts ?? []).length === 0 && (
              <tr>
                <td colSpan={2} style={{ textAlign: 'center', color: 'var(--ink-muted)' }}>
                  No one is currently clocked in.
                </td>
              </tr>
            )}
            {loading && (
              <tr>
                <td colSpan={2} style={{ textAlign: 'center', color: 'var(--ink-muted)' }}>
                  Loading…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div>
        <h2 className="section-title">Add employee</h2>
        <p className="section-sub">Onboard a new staff member with their own PIN.</p>
        <form className="form-card" onSubmit={addEmployee} style={{ marginTop: 'var(--space-3)' }}>
          <label className="field" style={{ flex: 1 }}>
            <span>Name</span>
            <input
              className="input"
              value={employeeDraft.name}
              onChange={(e) => setEmployeeDraft((prev) => ({ ...prev, name: e.target.value }))}
              placeholder="Jamie Rivera"
            />
          </label>
          <label className="field">
            <span>New PIN</span>
            <input
              className="input inline-input"
              type="password"
              inputMode="numeric"
              value={employeeDraft.pin}
              onChange={(e) => setEmployeeDraft((prev) => ({ ...prev, pin: e.target.value }))}
              placeholder="4-8 digits"
            />
          </label>
          <button className="btn btn-primary" type="submit" disabled={employeeBusy}>
            Add employee
          </button>
        </form>
        {employeeError && (
          <p className="form-error" role="alert" style={{ marginTop: 'var(--space-3)' }}>
            {employeeError}
          </p>
        )}
      </div>
    </section>
  );
}
