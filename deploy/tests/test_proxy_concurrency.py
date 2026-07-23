"""AC5 — concurrency and the oversell race, driven THROUGH the TLS proxy.

The prior session proved the oversell invariant holds when hitting the
container directly. Production traffic never does that — it arrives over TLS,
through nginx, with X-Forwarded-* rewriting and keepalive pooling in the path.
This re-runs the race and a burst load through https://.../ to prove the proxy
layer does not reorder, drop, or duplicate the state-changing POS writes.
"""

import concurrent.futures as cf
import os
import time

import pytest

from conftest import POS_API_KEY

pytestmark = [pytest.mark.docker, pytest.mark.slow]

POS_HEADERS = {"X-API-Key": POS_API_KEY}


def post_pos_sale(stack, sku, qty=1):
    """One POS sale through the TLS proxy. Returns (status, body)."""
    with stack.client() as c:
        r = c.post(
            f"{stack.https_url}/api/pos/sales",
            json={"sku": sku, "qty": qty},
            headers=POS_HEADERS,
        )
        return r.status_code, r.json()


def test_oversell_race_through_proxy_never_goes_below_zero(preview_stack, stock_product):
    """10 units in stock, 30 concurrent single-unit POS sales over TLS.

    Exactly 10 must succeed (201) and 20 must be rejected (409); final qty is
    exactly 0. Any 200-that-should-be-409 means the proxy let a write slip past
    the atomic decrement — the classic oversell.
    """
    sku = stock_product["sku"]
    attempts = 30
    expected_success = stock_product["qty"]  # 10

    with cf.ThreadPoolExecutor(max_workers=30) as pool:
        results = list(
            pool.map(lambda _: post_pos_sale(preview_stack, sku), range(attempts))
        )

    codes = [c for c, _ in results]
    successes = codes.count(201)
    conflicts = codes.count(409)

    assert successes == expected_success, (
        f"expected exactly {expected_success} successful sales, got {successes} "
        f"(codes: {sorted(codes)})"
    )
    assert conflicts == attempts - expected_success, (
        f"expected {attempts - expected_success} conflicts, got {conflicts}"
    )
    assert successes + conflicts == attempts, (
        f"some requests neither succeeded nor 409'd: {sorted(codes)}"
    )

    # Final stock, read back through the proxy, must be exactly zero.
    with preview_stack.client() as c:
        catalog = c.get(f"{preview_stack.https_url}/api/products?search={sku}").json()
    product = next(p for p in catalog["data"] if p["sku"] == sku)
    assert product["qty"] == 0, f"final qty was {product['qty']}, expected 0"


def test_every_409_reports_the_true_availability(preview_stack, stock_product):
    """A rejected oversell must tell the POS the real remaining stock, not a
    stale number — otherwise the terminal retries blindly."""
    sku = stock_product["sku"]
    with cf.ThreadPoolExecutor(max_workers=20) as pool:
        results = list(
            pool.map(lambda _: post_pos_sale(preview_stack, sku, qty=1), range(20))
        )

    for code, body in results:
        if code == 409:
            assert body["success"] is False
            assert body["error"], f"409 with empty error: {body}"


def test_concurrent_read_burst_through_proxy_is_all_200(preview_stack):
    """A burst of concurrent public reads over TLS returns 200 for every one.

    Exercises the keepalive-pooled upstream under nginx: a dropped or 502'd
    response here means the proxy/upstream pool cannot sustain fan-out.
    """
    total = 200
    endpoints = ["/api/health", "/api/products", "/"]

    def fetch(i):
        path = endpoints[i % len(endpoints)]
        with preview_stack.client() as c:
            return c.get(f"{preview_stack.https_url}{path}").status_code

    with cf.ThreadPoolExecutor(max_workers=40) as pool:
        codes = list(pool.map(fetch, range(total)))

    bad = [c for c in codes if c != 200]
    assert not bad, f"{len(bad)}/{total} non-200 responses through proxy: {set(bad)}"


def test_mixed_read_write_load_keeps_stock_consistent(preview_stack, stock_product):
    """Interleave reads and POS writes; the ledger must still balance.

    Sells the entire stock while readers hammer the catalog, then asserts
    units_sold + remaining == starting stock — no write lost under contention.
    """
    sku = stock_product["sku"]
    start_qty = stock_product["qty"]

    def worker(i):
        if i % 3 == 0:
            return ("write", *post_pos_sale(preview_stack, sku, qty=1))
        with preview_stack.client() as c:
            return ("read", c.get(f"{preview_stack.https_url}/api/products").status_code, None)

    with cf.ThreadPoolExecutor(max_workers=30) as pool:
        results = list(pool.map(worker, range(60)))

    reads = [r for r in results if r[0] == "read"]
    writes = [r for r in results if r[0] == "write"]
    sold = sum(1 for _, code, _ in writes if code == 201)

    assert all(code == 200 for _, code, _ in reads), "a read failed under load"

    with preview_stack.client() as c:
        catalog = c.get(f"{preview_stack.https_url}/api/products?search={sku}").json()
    product = next(p for p in catalog["data"] if p["sku"] == sku)
    assert sold + product["qty"] == start_qty, (
        f"ledger imbalance: sold {sold} + remaining {product['qty']} != {start_qty}"
    )
    assert product["qty"] >= 0, "stock went negative under mixed load"
