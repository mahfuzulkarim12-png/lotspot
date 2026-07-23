"""Fixtures for the deploy/ops tier.

These tests exercise the *shipped artefacts* — the built image, the nginx TLS
config in deploy/nginx/, and the public DNS/TLS for the preview slug — rather
than the Python app in-process. backend/tests already covers the app itself.

Everything is namespaced by PID and torn down in a finally-block, so parallel
or interrupted runs never collide and never leak containers.
"""

import json
import os
import shutil
import socket
import ssl
import subprocess
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
# Overridable so a negative-control run can point the same stack at a
# deliberately-broken config and prove the assertions have teeth.
NGINX_TEMPLATE = Path(
    os.environ.get(
        "LOTSPOT_NGINX_TEMPLATE",
        REPO_ROOT / "deploy" / "nginx" / "preview-tls.conf.template",
    )
)

IMAGE = os.environ.get("LOTSPOT_TEST_IMAGE", "lotspot-preview:latest")
PROXY_IMAGE = os.environ.get("LOTSPOT_TEST_PROXY_IMAGE", "nginx:alpine")

# The mission's preview slug: m<mission_number>-<short8>-preview.bohor.com.au
PREVIEW_SLUG = os.environ.get(
    "LOTSPOT_PREVIEW_SLUG", "m17-7cb5040d-preview.bohor.com.au"
)
PREVIEW_ZONE = "bohor.com.au"

ADMIN_USER = "admin"
ADMIN_PASSWORD = "deploy-test-pass"
POS_API_KEY = "deploy-test-pos-key"

_TAG = f"lotspot-deploytest-{os.getpid()}"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def docker(*args, check=True, capture=True, timeout=180):
    """Run a docker command. Returns CompletedProcess."""
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        docker("info", check=True, timeout=20)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def ca_context(ca_path):
    """An SSL context trusting only the given CA file (httpx-friendly)."""
    return ssl.create_default_context(cafile=str(ca_path))


def wait_for_http(url: str, timeout: float = 60.0, verify=True) -> bool:
    """Poll until url answers 200, or timeout. True if it came up.

    `verify` may be True, or a path to a CA bundle (converted to a context so
    httpx does not emit its verify=<str> deprecation warning).
    """
    verify_arg = ca_context(verify) if isinstance(verify, str) else verify
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=3.0, verify=verify_arg)
            if resp.status_code == 200:
                return True
        except (httpx.HTTPError, OSError):
            pass
        time.sleep(0.3)
    return False


@pytest.fixture(scope="session", autouse=True)
def _require_docker(request):
    """Every marker='docker' test needs a live daemon; fail loudly, not silently."""
    return None


def requires_docker():
    return pytest.mark.skipif(
        not docker_available(),
        reason="Docker daemon unreachable — start colima/Docker Desktop and re-run. "
        "This is an environment precondition, not a passing test.",
    )


# --------------------------------------------------------------------------
# TLS material
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def tls_certs():
    """Self-signed CA + leaf covering the preview slug, localhost and 127.0.0.1.

    Real preview TLS is a Google Trust Services wildcard fronted by Cloudflare
    (see test_preview_dns.py); this local pair exists so the *nginx config*
    under test can be exercised end-to-end without that infra.

    Written under the repo rather than pytest's tmp_path: the Linux VM backing
    Docker on macOS mounts $HOME, not /var/folders, so a cert in the system
    temp dir is invisible to the nginx container.
    """
    certs = REPO_ROOT / f".pytest-certs-{os.getpid()}"
    certs.mkdir(parents=True, exist_ok=True)
    key = certs / "privkey.pem"
    crt = certs / "fullchain.pem"

    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(crt),
            "-days", "2", "-subj", f"/CN={PREVIEW_SLUG}",
            "-addext",
            f"subjectAltName=DNS:{PREVIEW_SLUG},DNS:localhost,IP:127.0.0.1",
            "-addext", "keyUsage=critical,digitalSignature,keyEncipherment",
            "-addext", "extendedKeyUsage=serverAuth",
        ],
        check=True,
        capture_output=True,
    )
    os.chmod(key, 0o644)  # nginx in-container runs as a different uid
    try:
        yield {"dir": certs, "key": key, "cert": crt}
    finally:
        shutil.rmtree(certs, ignore_errors=True)


# --------------------------------------------------------------------------
# The stack: app container, optionally behind the real nginx TLS config
# --------------------------------------------------------------------------


class PreviewStack:
    def __init__(self, app_url, https_url, http_url, app_name, proxy_name, ca):
        self.app_url = app_url        # straight at the container
        self.https_url = https_url    # through nginx, TLS
        self.http_url = http_url      # through nginx, plaintext (redirects)
        self.app_name = app_name
        self.proxy_name = proxy_name
        self.ca = str(ca)             # CA bundle path (for tls_connect etc.)
        self._ca_ctx = ca_context(ca)

    def client(self, **kw):
        kw.setdefault("timeout", 10.0)
        return httpx.Client(verify=self._ca_ctx, **kw)

    def admin_token(self, base=None):
        base = base or self.https_url
        with self.client() as c:
            r = c.post(
                f"{base}/api/auth/login",
                json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
            )
            r.raise_for_status()
            return r.json()["data"]["token"]

    def admin_headers(self, base=None):
        return {"Authorization": f"Bearer {self.admin_token(base)}"}


def _cleanup(names, network):
    for name in names:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    if network:
        subprocess.run(["docker", "network", "rm", network], capture_output=True)


@pytest.fixture(scope="session")
def preview_stack(tls_certs):
    """App container + nginx TLS proxy on a private network. Torn down always."""
    if not docker_available():
        pytest.skip(
            "Docker daemon unreachable — cannot run the deploy tier. "
            "Start colima and re-run."
        )
    assert NGINX_TEMPLATE.exists(), f"missing nginx config: {NGINX_TEMPLATE}"

    network = f"{_TAG}-net"
    app_name = f"{_TAG}-app"
    proxy_name = f"{_TAG}-proxy"
    app_port = free_port()
    https_port = free_port()
    http_port = free_port()

    _cleanup([app_name, proxy_name], network)
    try:
        docker("network", "create", network)
        docker(
            "run", "-d", "--name", app_name, "--network", network,
            "-p", f"127.0.0.1:{app_port}:8080",
            "-e", f"LOTSPOT_ADMIN_USER={ADMIN_USER}",
            "-e", f"LOTSPOT_ADMIN_PASSWORD={ADMIN_PASSWORD}",
            "-e", f"LOTSPOT_POS_API_KEY={POS_API_KEY}",
            "-e", "LOTSPOT_SSE_HEARTBEAT=5",
            IMAGE,
        )
        app_url = f"http://127.0.0.1:{app_port}"
        if not wait_for_http(f"{app_url}/api/health", timeout=60):
            logs = docker("logs", app_name, check=False).stdout
            raise RuntimeError(f"app container never became healthy:\n{logs}")

        docker(
            "run", "-d", "--name", proxy_name, "--network", network,
            "-p", f"127.0.0.1:{https_port}:443",
            "-p", f"127.0.0.1:{http_port}:80",
            "-e", f"LOTSPOT_SERVER_NAME={PREVIEW_SLUG}",
            "-e", f"LOTSPOT_UPSTREAM={app_name}:8080",
            "-v", f"{NGINX_TEMPLATE}:/etc/nginx/templates/default.conf.template:ro",
            "-v", f"{tls_certs['dir']}:/etc/nginx/certs:ro",
            PROXY_IMAGE,
        )
        https_url = f"https://localhost:{https_port}"
        if not wait_for_http(
            f"{https_url}/api/health", timeout=45, verify=str(tls_certs["cert"])
        ):
            logs = docker("logs", proxy_name, check=False).stdout
            errs = docker("logs", proxy_name, check=False).stderr
            raise RuntimeError(f"nginx never served TLS:\n{logs}\n{errs}")

        yield PreviewStack(
            app_url=app_url,
            https_url=https_url,
            http_url=f"http://localhost:{http_port}",
            app_name=app_name,
            proxy_name=proxy_name,
            ca=tls_certs["cert"],
        )
    finally:
        _cleanup([app_name, proxy_name], network)


@pytest.fixture()
def stock_product(preview_stack):
    """A product with a known, isolated SKU. Deleted afterwards."""
    sku = f"DEPLOY-{os.getpid()}-{time.monotonic_ns() % 1_000_000}"
    headers = preview_stack.admin_headers()
    with preview_stack.client() as c:
        r = c.post(
            f"{preview_stack.https_url}/api/products",
            json={"sku": sku, "name": "Deploy Test Cola", "qty": 10, "price_cents": 250},
            headers=headers,
        )
        r.raise_for_status()
        product = r.json()["data"]
        try:
            yield product
        finally:
            c.delete(
                f"{preview_stack.https_url}/api/products/{product['id']}",
                headers=headers,
            )
