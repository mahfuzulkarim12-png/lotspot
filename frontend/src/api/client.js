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
