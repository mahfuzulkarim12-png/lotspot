const BASE_URL = process.env.LOTSPOT_BASE_URL || 'http://127.0.0.1:4322';
const ADMIN_USER = process.env.LOTSPOT_ADMIN_USER || 'admin';
const ADMIN_PASSWORD = process.env.LOTSPOT_ADMIN_PASSWORD || 'admin';
const POS_API_KEY = process.env.LOTSPOT_POS_API_KEY || 'demo-pos-key';

function uniqueSuffix() {
  return `${Date.now()}${Math.floor(Math.random() * 1000)}`;
}

async function apiRequest(path, { method = 'GET', body, token } = {}) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const envelope = await res.json();
  return { status: res.status, envelope };
}

async function apiLogin(username = ADMIN_USER, password = ADMIN_PASSWORD) {
  const { envelope } = await apiRequest('/api/auth/login', {
    method: 'POST',
    body: { username, password },
  });
  if (!envelope.success) throw new Error(`login failed: ${envelope.error}`);
  return envelope.data.token;
}

async function apiCreateProduct(token, { sku, name, qty, price_cents }) {
  const { envelope } = await apiRequest('/api/products', {
    method: 'POST',
    token,
    body: { sku, name, qty, price_cents },
  });
  if (!envelope.success) throw new Error(`create product failed: ${envelope.error}`);
  return envelope.data;
}

async function apiDeleteProduct(token, id) {
  await apiRequest(`/api/products/${id}`, { method: 'DELETE', token });
}

async function apiSalesSummary(token, date) {
  const { envelope } = await apiRequest(`/api/sales/summary?date=${encodeURIComponent(date)}`, { token });
  if (!envelope.success) throw new Error(`summary failed: ${envelope.error}`);
  return envelope.data;
}

async function apiSalesHistory(token, start, end) {
  const { envelope } = await apiRequest(
    `/api/sales/history?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
    { token }
  );
  if (!envelope.success) throw new Error(`history failed: ${envelope.error}`);
  return envelope.data;
}

// Mirrors frontend/src/lib/money.js formatCents exactly (incl. thousands grouping)
// so summary assertions stay correct as cumulative test-day totals grow.
function formatCents(cents) {
  const safe = Number.isFinite(cents) ? cents : 0;
  const dollars = safe / 100;
  return `$${dollars.toLocaleString('en', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function todayISO() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

async function apiPosSale({ sku, qty, unit_price_cents }) {
  const res = await fetch(`${BASE_URL}/api/pos/sales`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': POS_API_KEY },
    body: JSON.stringify({ sku, qty, ...(unit_price_cents ? { unit_price_cents } : {}) }),
  });
  const envelope = await res.json();
  return { status: res.status, envelope };
}

// Inventory rows switch their cells to <input> elements while editing, so a
// `hasText` locator (which matches rendered text, not input values) loses its
// match the instant "Edit" is clicked. Resolve the row's numeric position
// once, up front, and return an index-based locator that stays valid through
// the whole edit → save cycle.
async function stableRow(page, hasTextRowLocator) {
  const index = await hasTextRowLocator.evaluate((tr) => Array.from(tr.parentNode.children).indexOf(tr));
  return page.locator('tbody tr').nth(index);
}

// Selects the <option> whose visible text contains `name` inside the given
// <select> locator, without depending on Playwright's label-matching mode.
async function selectProductByName(selectLocator, name) {
  const value = await selectLocator.locator('option', { hasText: name }).getAttribute('value');
  if (!value) throw new Error(`No <option> found containing "${name}"`);
  await selectLocator.selectOption(value);
}

module.exports = {
  BASE_URL,
  ADMIN_USER,
  ADMIN_PASSWORD,
  POS_API_KEY,
  uniqueSuffix,
  apiRequest,
  apiLogin,
  apiCreateProduct,
  apiDeleteProduct,
  apiPosSale,
  apiSalesSummary,
  apiSalesHistory,
  todayISO,
  formatCents,
  selectProductByName,
  stableRow,
};
