import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { bpsToPercent, percentToBps } from '../lib/money';

const EMPTY_ACCOUNT_DRAFT = {
  name: '',
  jurisdiction: '',
  ratePercent: '',
  effectiveFrom: '',
  effectiveTo: '',
};

function accountDraftErrors(draft) {
  if (!draft.name.trim()) return 'Name is required';
  if (percentToBps(draft.ratePercent) === null) return 'Rate must be a valid percentage ≥ 0';
  if (!draft.effectiveFrom) return 'Effective from is required';
  if (draft.effectiveTo && draft.effectiveTo < draft.effectiveFrom) {
    return 'Effective to must be on or after effective from';
  }
  return null;
}

function accountPayload(draft) {
  return {
    name: draft.name.trim(),
    jurisdiction: draft.jurisdiction.trim() || null,
    rate_bps: percentToBps(draft.ratePercent),
    effective_from: draft.effectiveFrom,
    effective_to: draft.effectiveTo || null,
  };
}

export default function TaxSettingsPanel() {
  const [taxAccounts, setTaxAccounts] = useState(null);
  const [taxCategories, setTaxCategories] = useState(null);
  const [draft, setDraft] = useState(EMPTY_ACCOUNT_DRAFT);
  const [editingId, setEditingId] = useState(null);
  const [editDraft, setEditDraft] = useState(EMPTY_ACCOUNT_DRAFT);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [accounts, categories] = await Promise.all([
        api.listTaxAccounts(),
        api.listTaxCategories(),
      ]);
      setTaxAccounts(accounts);
      setTaxCategories(categories);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const set = (setter) => (field) => (e) =>
    setter((prev) => ({ ...prev, [field]: e.target.value }));
  const setNew = set(setDraft);
  const setEdit = set(setEditDraft);

  const addAccount = async (e) => {
    e.preventDefault();
    const problem = accountDraftErrors(draft);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.createTaxAccount(accountPayload(draft));
      setDraft(EMPTY_ACCOUNT_DRAFT);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const startEdit = (account) => {
    setEditingId(account.id);
    setEditDraft({
      name: account.name,
      jurisdiction: account.jurisdiction ?? '',
      ratePercent: bpsToPercent(account.rate_bps),
      effectiveFrom: account.effective_from,
      effectiveTo: account.effective_to ?? '',
    });
    setError(null);
  };

  const saveEdit = async () => {
    const problem = accountDraftErrors(editDraft);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.updateTaxAccount(editingId, accountPayload(editDraft));
      setEditingId(null);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const removeAccount = async (account) => {
    if (!window.confirm(`Delete the "${account.name}" tax account?`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteTaxAccount(account.id);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const toggleCategoryAccount = async (category, accountId) => {
    const current = new Set(category.tax_account_ids);
    if (current.has(accountId)) {
      current.delete(accountId);
    } else {
      current.add(accountId);
    }
    setBusy(true);
    setError(null);
    try {
      await api.setTaxCategoryAccounts(category.id, [...current]);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const loading = taxAccounts === null || taxCategories === null;

  return (
    <section aria-labelledby="tax-settings-heading">
      <h1 className="panel-title" id="tax-settings-heading">Tax settings</h1>
      <p className="panel-sub">
        Add each jurisdiction's rate as a tax account, then choose which accounts apply to
        each tax category below.
      </p>

      {error && (
        <p className="form-error" role="alert" style={{ marginTop: 'var(--space-3)' }}>
          {error}
        </p>
      )}

      <form className="form-card" onSubmit={addAccount} style={{ marginTop: 'var(--space-4)' }}>
        <label className="field" style={{ flex: 1 }}>
          <span>Name</span>
          <input className="input" value={draft.name} onChange={setNew('name')} placeholder="OK State" />
        </label>
        <label className="field" style={{ flex: 1 }}>
          <span>Jurisdiction</span>
          <input
            className="input"
            value={draft.jurisdiction}
            onChange={setNew('jurisdiction')}
            placeholder="Oklahoma"
          />
        </label>
        <label className="field">
          <span>Rate %</span>
          <input
            className="input inline-input"
            inputMode="decimal"
            value={draft.ratePercent}
            onChange={setNew('ratePercent')}
            placeholder="4.50"
          />
        </label>
        <label className="field">
          <span>Effective from</span>
          <input
            className="input"
            type="date"
            value={draft.effectiveFrom}
            onChange={setNew('effectiveFrom')}
          />
        </label>
        <label className="field">
          <span>Effective to</span>
          <input
            className="input"
            type="date"
            value={draft.effectiveTo}
            onChange={setNew('effectiveTo')}
          />
        </label>
        <button className="btn btn-primary" type="submit" disabled={busy}>
          Add tax account
        </button>
      </form>

      <div className="table-card" style={{ marginTop: 'var(--space-4)' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Jurisdiction</th>
              <th className="col-num">Rate</th>
              <th>Effective from</th>
              <th>Effective to</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {(taxAccounts ?? []).map((account) => {
              const isEditing = editingId === account.id;
              return (
                <tr key={account.id}>
                  {isEditing ? (
                    <>
                      <td><input className="input" value={editDraft.name} onChange={setEdit('name')} aria-label="Name" /></td>
                      <td><input className="input" value={editDraft.jurisdiction} onChange={setEdit('jurisdiction')} aria-label="Jurisdiction" /></td>
                      <td className="col-num"><input className="input inline-input" inputMode="decimal" value={editDraft.ratePercent} onChange={setEdit('ratePercent')} aria-label="Rate %" /></td>
                      <td><input className="input" type="date" value={editDraft.effectiveFrom} onChange={setEdit('effectiveFrom')} aria-label="Effective from" /></td>
                      <td><input className="input" type="date" value={editDraft.effectiveTo} onChange={setEdit('effectiveTo')} aria-label="Effective to" /></td>
                      <td>
                        <div className="row-actions">
                          <button className="btn btn-primary" type="button" onClick={saveEdit} disabled={busy}>Save</button>
                          <button className="btn" type="button" onClick={() => setEditingId(null)}>Cancel</button>
                        </div>
                      </td>
                    </>
                  ) : (
                    <>
                      <td>{account.name}</td>
                      <td>{account.jurisdiction || '—'}</td>
                      <td className="col-num num">{bpsToPercent(account.rate_bps)}%</td>
                      <td className="num">{account.effective_from}</td>
                      <td className="num">{account.effective_to || 'Ongoing'}</td>
                      <td>
                        <div className="row-actions">
                          <button className="btn" type="button" onClick={() => startEdit(account)}>Edit</button>
                          <button className="btn btn-danger" type="button" onClick={() => removeAccount(account)} disabled={busy}>Delete</button>
                        </div>
                      </td>
                    </>
                  )}
                </tr>
              );
            })}
            {!loading && (taxAccounts ?? []).length === 0 && (
              <tr>
                <td colSpan={6} style={{ textAlign: 'center', color: 'var(--ink-muted)' }}>
                  No tax accounts yet — add a jurisdiction's rate above.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <h2 className="section-title" style={{ marginTop: 'var(--space-6)' }}>Category mapping</h2>
      <p className="section-sub">
        Choose which tax accounts apply to each product tax category.
      </p>

      <div className="table-card" style={{ marginTop: 'var(--space-3)' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Category</th>
              {(taxAccounts ?? []).map((account) => (
                <th key={account.id} className="col-num">{account.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(taxCategories ?? []).map((category) => (
              <tr key={category.id}>
                <td>{category.name}</td>
                {(taxAccounts ?? []).map((account) => (
                  <td key={account.id} className="col-num">
                    <input
                      type="checkbox"
                      aria-label={`${category.name} applies ${account.name}`}
                      checked={category.tax_account_ids.includes(account.id)}
                      onChange={() => toggleCategoryAccount(category, account.id)}
                      disabled={busy}
                    />
                  </td>
                ))}
              </tr>
            ))}
            {!loading && (taxAccounts ?? []).length === 0 && (taxCategories ?? []).length > 0 && (
              <tr>
                <td colSpan={1 + (taxAccounts ?? []).length} style={{ textAlign: 'center', color: 'var(--ink-muted)' }}>
                  Add a tax account above to map it to categories.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
