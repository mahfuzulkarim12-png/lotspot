"""AC1 — public reachability of the preview slug on *.bohor.com.au.

Prior sessions recorded this as "no DNS/hosting available". That is not what
the network actually reports. These tests pin down which half is provisioned
and which half is not, so the handoff names the real missing piece:

  provisioned  : wildcard DNS for *.bohor.com.au (Cloudflare)
  provisioned  : wildcard TLS cert covering *.bohor.com.au
  NOT provisioned: an origin binding, so Cloudflare answers 502 for every slug

The last one is the deploy blocker and is marked `deploy_gate`.
"""

import datetime as dt
import socket
import ssl

import httpx
import pytest

from conftest import PREVIEW_SLUG, PREVIEW_ZONE

pytestmark = pytest.mark.network

# A label that nobody would ever provision explicitly. If this resolves, the
# zone is answering via a wildcard rather than per-slug records.
UNPROVISIONED_LABEL = f"zzq7-nonexistent-probe.{PREVIEW_ZONE}"

MIN_CERT_DAYS_REMAINING = 14


def resolve_a(hostname: str):
    """IPv4 addresses for hostname, or [] on NXDOMAIN."""
    try:
        infos = socket.getaddrinfo(hostname, 443, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return sorted({info[4][0] for info in infos})


def peer_cert(hostname: str, timeout: float = 10.0) -> dict:
    ctx = ssl.create_default_context()
    with socket.create_connection((hostname, 443), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=hostname) as tls:
            return tls.getpeercert()


def san_dns_names(cert: dict):
    return [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]


# --------------------------------------------------------------------------
# Provisioned today — these guard against a regression in the zone
# --------------------------------------------------------------------------


def test_preview_zone_resolves():
    """The bohor.com.au zone itself is live and answers A records."""
    addresses = resolve_a(PREVIEW_ZONE)
    assert addresses, f"{PREVIEW_ZONE} did not resolve to any IPv4 address"


def test_wildcard_covers_arbitrary_preview_slug():
    """Any *-preview.bohor.com.au label resolves, so no per-mission DNS record
    has to be created before a deploy — the wildcard already answers."""
    slug_addrs = resolve_a(PREVIEW_SLUG)
    assert slug_addrs, (
        f"{PREVIEW_SLUG} did not resolve. The wildcard record for "
        f"*.{PREVIEW_ZONE} is missing or was withdrawn."
    )

    control_addrs = resolve_a(UNPROVISIONED_LABEL)
    assert control_addrs == slug_addrs, (
        "Expected the slug and an arbitrary label to resolve identically "
        f"(wildcard). slug={slug_addrs} control={control_addrs}. If these "
        "differ, the slug now has a dedicated record — update this test."
    )


def test_wildcard_tls_cert_covers_preview_slug():
    """TLS for the slug is already valid — the edge presents a wildcard cert.

    This means a deploy does NOT need to provision a certificate; terminating
    TLS at the edge is already solved.
    """
    cert = peer_cert(PREVIEW_SLUG)
    names = san_dns_names(cert)
    assert f"*.{PREVIEW_ZONE}" in names, (
        f"cert for {PREVIEW_SLUG} does not cover *.{PREVIEW_ZONE}; SANs={names}"
    )


def test_wildcard_tls_cert_is_not_near_expiry():
    """Operational guard: fail while there is still time to rotate."""
    cert = peer_cert(PREVIEW_SLUG)
    not_after = dt.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=dt.timezone.utc
    )
    remaining = not_after - dt.datetime.now(dt.timezone.utc)
    assert remaining.days >= MIN_CERT_DAYS_REMAINING, (
        f"TLS cert for {PREVIEW_SLUG} expires in {remaining.days}d "
        f"({not_after.isoformat()}); rotate before it lapses."
    )


# --------------------------------------------------------------------------
# The actual blocker
# --------------------------------------------------------------------------


@pytest.mark.deploy_gate
def test_preview_slug_serves_lotspot_health():
    """The preview slug must serve THIS app's health endpoint.

    Currently RED: the edge resolves and terminates TLS but has no origin
    bound for *-preview.bohor.com.au, so it synthesises a 502. Fixing this is
    an infra action (bind a Cloudflare Tunnel / origin to the slug and run the
    already-built lotspot-preview image behind it), not a code change.
    """
    resp = httpx.get(f"https://{PREVIEW_SLUG}/api/health", timeout=15.0)

    assert resp.status_code == 200, (
        f"GET https://{PREVIEW_SLUG}/api/health returned {resp.status_code} "
        f"(server={resp.headers.get('server')!r}). "
        "DNS and TLS are provisioned; what is missing is an origin binding "
        "for the preview slug."
    )
    body = resp.json()
    assert body["success"] is True, f"unexpected envelope: {body}"


@pytest.mark.deploy_gate
def test_preview_slug_serves_customer_spa():
    """The customer view at / must be the LotSpot SPA, not an edge error page."""
    resp = httpx.get(f"https://{PREVIEW_SLUG}/", timeout=15.0)

    assert resp.status_code == 200, (
        f"GET https://{PREVIEW_SLUG}/ returned {resp.status_code}; "
        f"body={resp.text[:200]!r}"
    )
    assert "<!DOCTYPE html>" in resp.text[:200], (
        f"slug did not return an HTML document; got {resp.text[:200]!r}"
    )
