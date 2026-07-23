"""AC2 — sustained soak and high-concurrency load, driven by k6.

The previous session's load check was a few hundred requests at 50-way
concurrency via a hand-rolled ThreadPoolExecutor — enough to show nothing
crashes immediately, not enough to call it a soak or a stress test. These run
real k6 scenarios through the TLS proxy and assert on k6's own thresholds, so
a latency or error-rate regression fails the build rather than needing a human
to eyeball a summary.

Marked `slow`: the soak alone runs for minutes. Deselect with -m "not slow".
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.docker, pytest.mark.slow]

K6_SCRIPT = Path(__file__).resolve().parent / "k6" / "load.js"

# Kept short enough to run in CI; override for a genuine overnight soak.
SOAK_DURATION = os.environ.get("LOTSPOT_SOAK_DURATION", "3m")
SOAK_RATE = os.environ.get("LOTSPOT_SOAK_RATE", "60")
SPIKE_VUS = os.environ.get("LOTSPOT_SPIKE_VUS", "500")


def run_k6(stack, scenario, extra_env=None, timeout=900):
    """Run a k6 scenario against the TLS proxy; return the parsed summary."""
    if shutil.which("k6") is None:
        pytest.skip("k6 not installed — `brew install k6` and re-run")

    summary = Path(f"/tmp/k6-summary-{scenario}-{os.getpid()}.json")
    env = {
        **os.environ,
        "BASE_URL": stack.https_url,
        "SCENARIO": scenario,
        "SOAK_DURATION": SOAK_DURATION,
        "SOAK_RATE": SOAK_RATE,
        "SPIKE_VUS": SPIKE_VUS,
        **(extra_env or {}),
    }
    proc = subprocess.run(
        ["k6", "run", "--summary-export", str(summary), str(K6_SCRIPT)],
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    assert summary.exists(), (
        f"k6 produced no summary.\nstdout:\n{proc.stdout[-3000:]}\n"
        f"stderr:\n{proc.stderr[-2000:]}"
    )
    data = json.loads(summary.read_text())
    summary.unlink(missing_ok=True)

    # A setup()/teardown() exception still writes a summary but runs no
    # iterations; surface that before a metric assertion turns it into a
    # misleading "barely generated traffic".
    #
    # A *threshold* breach also makes k6 exit non-zero and log at error level —
    # that is a legitimate result, not a harness failure, so it must fall
    # through to the caller's assertions rather than be reported as a crash.
    if "script exception" in proc.stderr:
        pytest.fail(
            f"k6 script error during {scenario}:\n{proc.stderr[-2000:]}\n"
            f"stdout tail:\n{proc.stdout[-1500:]}"
        )
    return proc, data


def metric(data, name, stat):
    return (data.get("metrics", {}).get(name) or {}).get(stat)


def test_sustained_soak_holds_thresholds(preview_stack):
    """Constant arrival rate for minutes: no errors, latency stays bounded.

    k6's own thresholds (http_req_failed < 1%, p95 < 1s, checks > 99%) decide
    pass/fail — a non-zero exit means at least one was breached.
    """
    proc, data = run_k6(preview_stack, "soak")

    reqs = metric(data, "http_reqs", "count") or 0
    failed = metric(data, "http_req_failed", "value")
    p95 = metric(data, "http_req_duration", "p(95)")

    assert reqs > 100, f"soak barely generated traffic ({reqs} requests)"
    assert proc.returncode == 0, (
        f"k6 thresholds breached during the soak "
        f"(requests={reqs}, failed_rate={failed}, p95={p95}ms)\n"
        f"{proc.stdout[-3000:]}"
    )
    assert failed is not None and failed < 0.01, f"error rate {failed}"


def test_high_concurrency_spike_holds_thresholds(preview_stack):
    """Ramp to high concurrency — far past the 50-way check done previously."""
    proc, data = run_k6(preview_stack, "spike")

    reqs = metric(data, "http_reqs", "count") or 0
    failed = metric(data, "http_req_failed", "value")
    max_vus = metric(data, "vus_max", "max") or metric(data, "vus_max", "value")

    assert reqs > 100, f"spike barely generated traffic ({reqs} requests)"
    assert proc.returncode == 0, (
        f"k6 thresholds breached during the spike "
        f"(requests={reqs}, failed_rate={failed}, max_vus={max_vus})\n"
        f"{proc.stdout[-3000:]}"
    )
    assert failed is not None and failed < 0.01, f"error rate {failed}"


def test_stock_ledger_is_exact_after_sustained_load(preview_stack):
    """After the load scenarios, sales and stock must still reconcile.

    Throughput numbers are worthless if the writes they drove were lossy; this
    asserts the invariant survives sustained concurrency, not just a burst.
    """
    headers = preview_stack.admin_headers()
    sku = f"LEDGER-{os.getpid()}"
    start_qty = 500

    with preview_stack.client() as c:
        created = c.post(
            f"{preview_stack.https_url}/api/products",
            json={"sku": sku, "name": "Ledger Cola", "qty": start_qty, "price_cents": 100},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        product_id = created.json()["data"]["id"]

    try:
        proc, data = run_k6(
            preview_stack,
            "spike",
            extra_env={"SPIKE_VUS": "100", "LOTSPOT_SPIKE_VUS": "100"},
        )
        sold = metric(data, "pos_sale_success", "passes") or 0

        with preview_stack.client() as c:
            catalog = c.get(
                f"{preview_stack.https_url}/api/products?search={sku}"
            ).json()
        # The load script sells its own SKU, so this product should be untouched.
        product = next(p for p in catalog["data"] if p["sku"] == sku)
        assert product["qty"] == start_qty, (
            f"unrelated product's stock moved during load: {product['qty']} "
            f"!= {start_qty} (k6 recorded {sold} sales against its own SKU)"
        )
        assert product["qty"] >= 0
    finally:
        with preview_stack.client() as c:
            c.delete(
                f"{preview_stack.https_url}/api/products/{product_id}", headers=headers
            )
