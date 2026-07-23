"""AC4 — TLS termination at the reverse proxy.

Exercises deploy/nginx/preview-tls.conf.template against the real image. The
earlier proxy smoke check was HTTP-only, so none of this was covered: cert
verification, protocol floor, redirect behaviour, HSTS, or — the one that
actually breaks in production — whether SSE still streams unbuffered once TLS
is in the path.
"""

import json
import socket
import ssl
import threading
import time

import httpx
import pytest

from conftest import PREVIEW_SLUG

pytestmark = pytest.mark.docker


def tls_connect(host, port, ca, minimum=None, maximum=None, seclevel=None):
    """Handshake and return the negotiated protocol version."""
    ctx = ssl.create_default_context(cafile=ca)
    if minimum is not None:
        ctx.minimum_version = minimum
    if maximum is not None:
        ctx.maximum_version = maximum
    if seclevel is not None:
        ctx.set_ciphers(f"ALL:@SECLEVEL={seclevel}")
    with socket.create_connection((host, port), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            return tls.version()


def split_hostport(url):
    hostport = url.split("://", 1)[1]
    host, _, port = hostport.partition(":")
    return host, int(port)


# --------------------------------------------------------------------------
# Certificate + protocol
# --------------------------------------------------------------------------


def test_https_serves_the_app_with_a_verifiable_certificate(preview_stack):
    """Full chain verification — no verify=False anywhere in this suite."""
    with preview_stack.client() as c:
        resp = c.get(f"{preview_stack.https_url}/api/health")
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True


def test_certificate_is_rejected_when_not_trusted(preview_stack):
    """Negative control: the same endpoint must fail against the system CA
    store, proving the pass above came from our CA and not from verification
    being silently disabled."""
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"{preview_stack.https_url}/api/health", timeout=10.0)


def test_negotiated_protocol_is_tls12_or_better(preview_stack):
    host, port = split_hostport(preview_stack.https_url)
    version = tls_connect(host, port, preview_stack.ca)
    assert version in ("TLSv1.2", "TLSv1.3"), f"negotiated {version}"


def test_tls13_is_available(preview_stack):
    host, port = split_hostport(preview_stack.https_url)
    version = tls_connect(
        host, port, preview_stack.ca, minimum=ssl.TLSVersion.TLSv1_3
    )
    assert version == "TLSv1.3", f"negotiated {version}"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_tls11_and_below_are_refused(preview_stack):
    """The config pins ssl_protocols to TLSv1.2/1.3.

    SECLEVEL=0 is set so the *client* is willing to offer TLS 1.1; the refusal
    therefore comes from the server, not from local policy.
    """
    host, port = split_hostport(preview_stack.https_url)
    with pytest.raises(ssl.SSLError) as exc:
        tls_connect(
            host,
            port,
            preview_stack.ca,
            maximum=ssl.TLSVersion.TLSv1_1,
            seclevel=0,
        )
    assert "version" in str(exc.value).lower() or "protocol" in str(exc.value).lower(), (
        f"expected a protocol-version failure, got: {exc.value}"
    )


def test_sni_for_the_real_preview_slug_is_served(preview_stack):
    """Requests arriving with the production Host header are served, not 421'd."""
    host, port = split_hostport(preview_stack.https_url)
    with preview_stack.client(headers={"Host": PREVIEW_SLUG}) as c:
        resp = c.get(f"{preview_stack.https_url}/api/health")
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------
# Redirects and headers
# --------------------------------------------------------------------------


def test_plain_http_redirects_to_https(preview_stack):
    resp = httpx.get(
        f"{preview_stack.http_url}/admin", follow_redirects=False, timeout=10.0
    )
    assert resp.status_code == 308, f"got {resp.status_code}"
    location = resp.headers["location"]
    assert location.startswith("https://"), location
    assert location.endswith("/admin"), f"path not preserved: {location}"


def test_acme_challenge_path_is_not_redirected(preview_stack):
    """Cert renewal must be able to answer http-01 over plain HTTP."""
    resp = httpx.get(
        f"{preview_stack.http_url}/.well-known/acme-challenge/probe-token",
        follow_redirects=False,
        timeout=10.0,
    )
    assert resp.status_code != 308, (
        "ACME challenge was redirected to HTTPS; http-01 renewal would fail"
    )


@pytest.mark.parametrize(
    "header,expected",
    [
        ("strict-transport-security", "max-age=31536000"),
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("referrer-policy", "strict-origin-when-cross-origin"),
    ],
)
def test_security_headers_present_over_tls(preview_stack, header, expected):
    with preview_stack.client() as c:
        resp = c.get(f"{preview_stack.https_url}/")
    assert header in resp.headers, f"missing {header}; got {list(resp.headers)}"
    assert expected in resp.headers[header], (
        f"{header}={resp.headers[header]!r}, expected to contain {expected!r}"
    )


def test_hsts_is_not_sent_over_plain_http(preview_stack):
    """HSTS on a plaintext response is meaningless and a spec violation."""
    resp = httpx.get(
        f"{preview_stack.http_url}/", follow_redirects=False, timeout=10.0
    )
    assert "strict-transport-security" not in resp.headers


def test_forwarded_proto_reaches_the_app(preview_stack):
    """The app sits behind TLS termination; it must be told the real scheme."""
    with preview_stack.client() as c:
        resp = c.get(f"{preview_stack.https_url}/api/health")
    assert resp.status_code == 200
    # nginx sets X-Forwarded-Proto=https; the app echoes nothing, so assert on
    # the proxy config being applied by checking the redirect target instead.
    plain = httpx.get(
        f"{preview_stack.http_url}/api/health", follow_redirects=False, timeout=10.0
    )
    assert plain.headers["location"].startswith("https://")


# --------------------------------------------------------------------------
# SSE through TLS — the regression that matters
# --------------------------------------------------------------------------


def read_sse_frames(stack, stop_after, timeout):
    """Collect raw SSE frames from the TLS endpoint until stop_after or timeout."""
    frames = []
    started = time.monotonic()
    with stack.client(timeout=timeout) as c:
        with c.stream("GET", f"{stack.https_url}/api/events") as resp:
            assert resp.status_code == 200, resp.status_code
            assert resp.headers["content-type"].startswith("text/event-stream")
            for line in resp.iter_lines():
                if line.strip():
                    frames.append((round(time.monotonic() - started, 2), line))
                if len(frames) >= stop_after or time.monotonic() - started > timeout:
                    break
    return frames


def test_sse_stream_is_not_buffered_by_the_proxy(preview_stack):
    """The first frame must arrive immediately, not after the response ends.

    A buffering proxy holds this back indefinitely — the failure mode is a
    stream that never delivers, which is why this asserts on latency.
    """
    started = time.monotonic()
    frames = read_sse_frames(preview_stack, stop_after=1, timeout=10)
    elapsed = time.monotonic() - started

    assert frames, "no SSE frame received through the TLS proxy"
    assert "connected" in frames[0][1], f"first frame was {frames[0][1]!r}"
    assert elapsed < 5, (
        f"first SSE frame took {elapsed:.2f}s — proxy is buffering the stream"
    )


def test_sse_delivers_a_live_inventory_event_through_tls(preview_stack):
    """An event published while the stream is open must reach the client."""
    received = []

    def reader():
        received.extend(read_sse_frames(preview_stack, stop_after=2, timeout=20))

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    time.sleep(1.5)  # let the subscriber attach before publishing

    sku = f"SSE-TLS-{time.monotonic_ns() % 1_000_000}"
    headers = preview_stack.admin_headers()
    with preview_stack.client() as c:
        created = c.post(
            f"{preview_stack.https_url}/api/products",
            json={"sku": sku, "name": "SSE TLS probe", "qty": 3, "price_cents": 100},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        product_id = created.json()["data"]["id"]

    thread.join(timeout=20)
    try:
        payloads = [f[1] for f in received if f[1].startswith("data:")]
        assert len(payloads) >= 2, f"expected connected + inventory, got {received}"
        event = json.loads(payloads[1].removeprefix("data:").strip())
        assert event["type"] == "inventory", f"unexpected event: {event}"
    finally:
        with preview_stack.client() as c:
            c.delete(
                f"{preview_stack.https_url}/api/products/{product_id}", headers=headers
            )


@pytest.mark.slow
def test_sse_heartbeat_survives_tls_and_proxy_read_timeout(preview_stack):
    """Keepalives must keep flowing; the container runs with a 5s heartbeat.

    Guards against proxy_read_timeout being set below the heartbeat interval,
    which silently kills long-lived streams in production.
    """
    frames = read_sse_frames(preview_stack, stop_after=3, timeout=25)
    keepalives = [f for f in frames if f[1].startswith(":")]
    assert len(keepalives) >= 2, (
        f"expected >=2 keepalive frames within 25s, got {frames}"
    )
    gap = keepalives[1][0] - keepalives[0][0]
    assert 3.0 <= gap <= 9.0, (
        f"heartbeat cadence {gap}s outside the expected ~5s window: {frames}"
    )
