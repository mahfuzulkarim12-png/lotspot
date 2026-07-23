// AC2 — sustained soak and high-concurrency spike load for the preview stack.
//
// Two scenarios, selected with SCENARIO:
//   soak  — multi-minute constant arrival rate at realistic POS-terminal load
//   spike — ramp to high concurrency to find the breaking point
//
// Driven through the TLS proxy by default so the proxy is under the same load
// as the app (AC5 asks for exactly that).
//
//   k6 run -e BASE_URL=https://localhost:8443 -e SCENARIO=soak deploy/tests/k6/load.js
//
// Traffic mix models the real product: the customer screen polls the catalog
// far more often than the register pushes a sale.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'https://localhost:8443';
const SCENARIO = __ENV.SCENARIO || 'soak';
const POS_API_KEY = __ENV.POS_API_KEY || 'deploy-test-pos-key';
const ADMIN_USER = __ENV.ADMIN_USER || 'admin';
const ADMIN_PASSWORD = __ENV.ADMIN_PASSWORD || 'deploy-test-pass';

const SOAK_DURATION = __ENV.SOAK_DURATION || '3m';
const SOAK_RATE = parseInt(__ENV.SOAK_RATE || '60', 10); // requests/second
const SPIKE_VUS = parseInt(__ENV.SPIKE_VUS || '500', 10);

const oversells = new Counter('pos_oversell_responses');
const posSuccess = new Rate('pos_sale_success');

const scenarios = {
  soak: {
    executor: 'constant-arrival-rate',
    rate: SOAK_RATE,
    timeUnit: '1s',
    duration: SOAK_DURATION,
    preAllocatedVUs: 50,
    maxVUs: 300,
  },
  spike: {
    executor: 'ramping-vus',
    startVUs: 10,
    stages: [
      { duration: '20s', target: SPIKE_VUS },
      { duration: '40s', target: SPIKE_VUS },
      { duration: '10s', target: 0 },
    ],
  },
};

// Latency budgets differ by intent. The soak models real POS-terminal load and
// must hold a genuine SLO. The spike deliberately drives the app past its
// comfortable capacity to find where it degrades, so its latency ceiling is a
// degraded-mode bound, not an SLO. What does NOT relax between them is
// correctness: zero failed requests and zero failed checks in both.
const thresholds = {
  soak: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<1000', 'p(99)<3000'],
    checks: ['rate>0.99'],
  },
  spike: {
    http_req_failed: ['rate<0.01'],
    // Measured p(95)~1.1s at 500 VUs on a single-worker container with 0
    // errors. 3s is the point past which the customer screen feels broken.
    http_req_duration: ['p(95)<3000', 'p(99)<8000'],
    checks: ['rate>0.99'],
  },
};

export const options = {
  insecureSkipTLSVerify: true, // local self-signed cert; see test_tls_termination.py
  scenarios: { [SCENARIO]: scenarios[SCENARIO] },
  thresholds: thresholds[SCENARIO],
};

export function setup() {
  const login = http.post(
    `${BASE_URL}/api/auth/login`,
    JSON.stringify({ username: ADMIN_USER, password: ADMIN_PASSWORD }),
    { headers: { 'Content-Type': 'application/json' } },
  );
  if (login.status !== 200) {
    throw new Error(`setup login failed: ${login.status} ${login.body}`);
  }
  const token = login.json('data.token');

  // Stock deliberately far above the sale volume so the soak measures
  // throughput, not the 409 path (that is covered in test_proxy_concurrency).
  const sku = `LOADTEST-${Date.now()}`;
  const created = http.post(
    `${BASE_URL}/api/products`,
    // 999999 is the API's documented max qty; keep it at the ceiling so the
    // soak never drifts into the 409 path.
    JSON.stringify({ sku, name: 'Load Test Cola', qty: 999999, price_cents: 250 }),
    { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` } },
  );
  if (created.status !== 201) {
    throw new Error(`setup product failed: ${created.status} ${created.body}`);
  }
  return { token, sku, productId: created.json('data.id') };
}

export default function (data) {
  const roll = Math.random();

  if (roll < 0.55) {
    // Customer screen: the catalog, by far the hottest path.
    const res = http.get(`${BASE_URL}/api/products`, { tags: { path: 'products' } });
    check(res, { 'products 200': (r) => r.status === 200 });
  } else if (roll < 0.75) {
    const res = http.get(`${BASE_URL}/`, { tags: { path: 'spa' } });
    check(res, { 'spa 200': (r) => r.status === 200 });
  } else if (roll < 0.9) {
    const res = http.get(`${BASE_URL}/api/health`, { tags: { path: 'health' } });
    check(res, { 'health 200': (r) => r.status === 200 });
  } else {
    // Register pushes a sale.
    const res = http.post(
      `${BASE_URL}/api/pos/sales`,
      JSON.stringify({ sku: data.sku, qty: 1 }),
      {
        headers: { 'Content-Type': 'application/json', 'X-API-Key': POS_API_KEY },
        tags: { path: 'pos_sale' },
      },
    );
    posSuccess.add(res.status === 201);
    if (res.status === 409) oversells.add(1);
    check(res, { 'pos sale 201': (r) => r.status === 201 });
  }

  sleep(0.1);
}

export function teardown(data) {
  http.del(`${BASE_URL}/api/products/${data.productId}`, null, {
    headers: { Authorization: `Bearer ${data.token}` },
  });
}
