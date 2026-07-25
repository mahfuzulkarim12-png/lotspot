// Thin fetch wrapper around the LotSpot API envelope:
// every response is {success, data, error}; failures throw ApiError.

const TOKEN_KEY = 'lotspot.token';

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

async function request(path, { method = 'GET', body, auth = false } = {}) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth) {
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  let envelope;
  try {
    envelope = await response.json();
  } catch {
    throw new ApiError(`Unexpected response from server (${response.status})`, response.status);
  }

  if (!response.ok || !envelope.success) {
    throw new ApiError(envelope.error || `Request failed (${response.status})`, response.status);
  }
  return envelope.data;
}

export const api = {
  login: (username, password) =>
    request('/api/auth/login', { method: 'POST', body: { username, password } }),
  logout: () => request('/api/auth/logout', { method: 'POST', auth: true }),
  me: () => request('/api/auth/me', { auth: true }),

  listProducts: () => request('/api/products'),
  createProduct: (product) =>
    request('/api/products', { method: 'POST', body: product, auth: true }),
  updateProduct: (id, changes) =>
    request(`/api/products/${id}`, { method: 'PUT', body: changes, auth: true }),
  deleteProduct: (id) =>
    request(`/api/products/${id}`, { method: 'DELETE', auth: true }),

  createSale: (sale) => request('/api/sales', { method: 'POST', body: sale, auth: true }),
  posCheckout: (checkout) =>
    request('/api/pos/checkout', { method: 'POST', body: checkout, auth: true }),
  listSales: (date) =>
    request(`/api/sales?date=${encodeURIComponent(date)}`, { auth: true }),
  salesSummary: (date) =>
    request(`/api/sales/summary?date=${encodeURIComponent(date)}`, { auth: true }),
  salesHistory: (start, end) =>
    request(
      `/api/sales/history?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
      { auth: true }
    ),

  voidSale: (saleId, reason) =>
    request(`/api/sales/${saleId}/void`, {
      method: 'POST',
      body: { reason: reason ?? null },
      auth: true,
    }),
  voidReceipt: (transactionId, reason) =>
    request(`/api/sales/transactions/${encodeURIComponent(transactionId)}/void`, {
      method: 'POST',
      body: { reason: reason ?? null },
      auth: true,
    }),
  voidedReceipts: ({ start, end, q } = {}) => {
    const params = new URLSearchParams();
    if (start) params.set('start', start);
    if (end) params.set('end', end);
    if (q) params.set('q', q);
    const query = params.toString();
    return request(`/api/sales/voided/receipts${query ? `?${query}` : ''}`, { auth: true });
  },
  voidedItems: ({ start, end, q } = {}) => {
    const params = new URLSearchParams();
    if (start) params.set('start', start);
    if (end) params.set('end', end);
    if (q) params.set('q', q);
    const query = params.toString();
    return request(`/api/sales/voided/items${query ? `?${query}` : ''}`, { auth: true });
  },

  listTaxCategories: () => request('/api/tax-categories', { auth: true }),
  createTaxCategory: (category) =>
    request('/api/tax-categories', { method: 'POST', body: category, auth: true }),
  updateTaxCategory: (id, changes) =>
    request(`/api/tax-categories/${id}`, { method: 'PUT', body: changes, auth: true }),
  deleteTaxCategory: (id) =>
    request(`/api/tax-categories/${id}`, { method: 'DELETE', auth: true }),
  setTaxCategoryAccounts: (id, taxAccountIds) =>
    request(`/api/tax-categories/${id}/tax-accounts`, {
      method: 'PUT',
      body: { tax_account_ids: taxAccountIds },
      auth: true,
    }),

  listTaxAccounts: () => request('/api/tax-accounts', { auth: true }),
  createTaxAccount: (account) =>
    request('/api/tax-accounts', { method: 'POST', body: account, auth: true }),
  updateTaxAccount: (id, changes) =>
    request(`/api/tax-accounts/${id}`, { method: 'PUT', body: changes, auth: true }),
  deleteTaxAccount: (id) =>
    request(`/api/tax-accounts/${id}`, { method: 'DELETE', auth: true }),

  listEmployees: () => request('/api/employees', { auth: true }),
  createEmployee: (employee) =>
    request('/api/employees', { method: 'POST', body: employee, auth: true }),

  clockIn: (employeeId, pin) =>
    request('/api/timeclock/clock-in', {
      method: 'POST',
      body: { employee_id: employeeId, pin },
      auth: true,
    }),
  clockOut: (employeeId, pin) =>
    request('/api/timeclock/clock-out', {
      method: 'POST',
      body: { employee_id: employeeId, pin },
      auth: true,
    }),
  timeclockStatus: () => request('/api/timeclock/status', { auth: true }),
  timeclockHistory: ({ employeeId, start, end } = {}) => {
    const params = new URLSearchParams();
    if (employeeId) params.set('employee_id', employeeId);
    if (start) params.set('start', start);
    if (end) params.set('end', end);
    const query = params.toString();
    return request(`/api/timeclock/history${query ? `?${query}` : ''}`, { auth: true });
  },
};
