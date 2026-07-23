import { useEffect, useMemo, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { api } from '../api/client';
import { dollarsToCents, formatCents } from '../lib/money';
import { stockStatus } from '../lib/inventory';

const BADGE_LABEL = { ok: 'In stock', low: 'Low', out: 'Out' };
const GENERAL_MERCHANDISE_NAME = 'General Merchandise';
const EMPTY_DRAFT = { sku: '', name: '', qty: '', price: '', taxCategoryId: '' };

function draftErrors(draft) {
  if (!draft.sku.trim()) return 'SKU is required';
  if (!draft.name.trim()) return 'Name is required';
  const qty = Number(draft.qty);
  if (!Number.isInteger(qty) || qty < 0) return 'Qty must be a whole number ≥ 0';
  if (dollarsToCents(draft.price) === null) return 'Price must be a valid amount';
  return null;
}

export default function InventoryPanel() {
  const { products } = useOutletContext();
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [editingId, setEditingId] = useState(null);
  const [editDraft, setEditDraft] = useState(EMPTY_DRAFT);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [taxCategories, setTaxCategories] = useState([]);

  useEffect(() => {
    api.listTaxCategories().then(setTaxCategories).catch(() => {});
  }, []);

  // Pre-select General Merchandise (or the first category) on the create
  // form once categories load, without clobbering an in-progress edit.
  useEffect(() => {
    if (taxCategories.length === 0) return;
    setDraft((prev) => {
      if (prev.taxCategoryId) return prev;
      const fallback =
        taxCategories.find((c) => c.name === GENERAL_MERCHANDISE_NAME) ?? taxCategories[0];
      return fallback ? { ...prev, taxCategoryId: String(fallback.id) } : prev;
    });
  }, [taxCategories]);

  const taxCategoryName = useMemo(() => {
    const byId = new Map(taxCategories.map((c) => [c.id, c.name]));
    return (id) => byId.get(id) ?? '—';
  }, [taxCategories]);

  const set = (setter) => (field) => (e) =>
    setter((prev) => ({ ...prev, [field]: e.target.value }));
  const setNew = set(setDraft);
  const setEdit = set(setEditDraft);

  const addProduct = async (e) => {
    e.preventDefault();
    const problem = draftErrors(draft);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const payload = {
        sku: draft.sku.trim(),
        name: draft.name.trim(),
        qty: Number(draft.qty),
        price_cents: dollarsToCents(draft.price),
      };
      if (draft.taxCategoryId) payload.tax_category_id = Number(draft.taxCategoryId);
      await api.createProduct(payload);
      setDraft((prev) => ({ ...EMPTY_DRAFT, taxCategoryId: prev.taxCategoryId })); // list updates via SSE
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const startEdit = (product) => {
    setEditingId(product.id);
    setEditDraft({
      sku: product.sku,
      name: product.name,
      qty: String(product.qty),
      price: (product.price_cents / 100).toFixed(2),
      taxCategoryId: product.tax_category_id != null ? String(product.tax_category_id) : '',
    });
    setError(null);
  };

  const saveEdit = async () => {
    const problem = draftErrors(editDraft);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.updateProduct(editingId, {
        sku: editDraft.sku.trim(),
        name: editDraft.name.trim(),
        qty: Number(editDraft.qty),
        price_cents: dollarsToCents(editDraft.price),
        tax_category_id: editDraft.taxCategoryId ? Number(editDraft.taxCategoryId) : null,
      });
      setEditingId(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (product) => {
    if (!window.confirm(`Delete “${product.name}” (${product.sku})? Sales history is kept.`)) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.deleteProduct(product.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="inventory-heading">
      <h1 className="panel-title" id="inventory-heading">Inventory</h1>
      <p className="panel-sub">
        Changes here appear on the customer screen instantly.
      </p>

      <form className="form-card" onSubmit={addProduct} style={{ marginTop: 'var(--space-4)' }}>
        <label className="field">
          <span>SKU</span>
          <input className="input" value={draft.sku} onChange={setNew('sku')} placeholder="COKE-330" />
        </label>
        <label className="field" style={{ flex: 1 }}>
          <span>Name</span>
          <input className="input" value={draft.name} onChange={setNew('name')} placeholder="Coca-Cola 330ml" />
        </label>
        <label className="field">
          <span>Qty</span>
          <input className="input inline-input" inputMode="numeric" value={draft.qty} onChange={setNew('qty')} placeholder="24" />
        </label>
        <label className="field">
          <span>Price</span>
          <input className="input inline-input" inputMode="decimal" value={draft.price} onChange={setNew('price')} placeholder="2.50" />
        </label>
        <label className="field">
          <span>Tax category</span>
          <select className="input" value={draft.taxCategoryId} onChange={setNew('taxCategoryId')}>
            {draft.taxCategoryId === '' && <option value="">Loading…</option>}
            {taxCategories.map((category) => (
              <option key={category.id} value={category.id}>
                {category.name}
              </option>
            ))}
          </select>
        </label>
        <button className="btn btn-primary" type="submit" disabled={busy}>
          Add product
        </button>
      </form>

      {error && (
        <p className="form-error" role="alert" style={{ marginTop: 'var(--space-3)' }}>
          {error}
        </p>
      )}

      <div className="table-card" style={{ marginTop: 'var(--space-4)' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>SKU</th>
              <th>Name</th>
              <th className="col-num">Qty</th>
              <th className="col-num">Price</th>
              <th>Tax category</th>
              <th>Status</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {(products ?? []).map((product) => {
              const status = stockStatus(product.qty);
              const isEditing = editingId === product.id;
              return (
                <tr key={product.id}>
                  {isEditing ? (
                    <>
                      <td><input className="input inline-input" value={editDraft.sku} onChange={setEdit('sku')} aria-label="SKU" /></td>
                      <td><input className="input" value={editDraft.name} onChange={setEdit('name')} aria-label="Name" /></td>
                      <td className="col-num"><input className="input inline-input" inputMode="numeric" value={editDraft.qty} onChange={setEdit('qty')} aria-label="Qty" /></td>
                      <td className="col-num"><input className="input inline-input" inputMode="decimal" value={editDraft.price} onChange={setEdit('price')} aria-label="Price" /></td>
                      <td>
                        <select
                          className="input inline-input"
                          value={editDraft.taxCategoryId}
                          onChange={setEdit('taxCategoryId')}
                          aria-label="Tax category"
                        >
                          <option value="">No tax category</option>
                          {taxCategories.map((category) => (
                            <option key={category.id} value={category.id}>
                              {category.name}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td><span className={`badge badge-${status}`}>{BADGE_LABEL[status]}</span></td>
                      <td>
                        <div className="row-actions">
                          <button className="btn btn-primary" type="button" onClick={saveEdit} disabled={busy}>Save</button>
                          <button className="btn" type="button" onClick={() => setEditingId(null)}>Cancel</button>
                        </div>
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="num">{product.sku}</td>
                      <td>{product.name}</td>
                      <td className="col-num num">{product.qty}</td>
                      <td className="col-num num">{formatCents(product.price_cents)}</td>
                      <td>{taxCategoryName(product.tax_category_id)}</td>
                      <td><span className={`badge badge-${status}`}>{BADGE_LABEL[status]}</span></td>
                      <td>
                        <div className="row-actions">
                          <button className="btn" type="button" onClick={() => startEdit(product)}>Edit</button>
                          <button className="btn btn-danger" type="button" onClick={() => remove(product)} disabled={busy}>Delete</button>
                        </div>
                      </td>
                    </>
                  )}
                </tr>
              );
            })}
            {(products ?? []).length === 0 && (
              <tr>
                <td colSpan={7} style={{ textAlign: 'center', color: 'var(--ink-muted)' }}>
                  No products yet — add the first one above.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
