"""AC6 — failure injection: OOM-kill / SIGKILL / restart mid-request.

None of this was covered before. The questions a store owner actually cares
about the night the POS box wedges:

  * If the kernel OOM-kills the container (SIGKILL, no graceful shutdown), does
    the restart policy bring it back on its own?
  * Does a sale committed one second before the kill survive the restart, or is
    the SQLite file left corrupt / rolled back?
  * If a client is mid-request when the container dies, does it get a clean
    connection error, or hang forever?

These run their OWN containers (custom memory limit, restart policy, mounted
volume) rather than the shared preview_stack, and always tear down.
"""

import os
import subprocess
import threading
import time

import httpx
import pytest

from conftest import (
    ADMIN_PASSWORD,
    ADMIN_USER,
    IMAGE,
    POS_API_KEY,
    docker,
    docker_available,
    free_port,
    wait_for_http,
)

pytestmark = [pytest.mark.docker, pytest.mark.slow]

_TAG = f"lotspot-chaos-{os.getpid()}"


def _admin_headers(base):
    r = httpx.post(
        f"{base}/api/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        timeout=10,
    )
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _container_state(name):
    out = docker(
        "inspect", "-f", "{{.State.Status}}|{{.State.OOMKilled}}|{{.RestartCount}}",
        name, check=False,
    )
    return out.stdout.strip()


@pytest.fixture()
def chaos_container():
    """A single app container with a named volume + restart policy, torn down
    with its volume afterwards. Parametrised per-test via the returned helper.
    """
    if not docker_available():
        pytest.skip("Docker daemon unreachable — cannot run chaos tier.")

    created = {"name": None, "volume": None, "port": None}

    def _start(memory=None, restart="unless-stopped", volume=None):
        name = f"{_TAG}-{int(time.monotonic() * 1000) % 1_000_000}"
        port = free_port()
        args = [
            "run", "-d", "--name", name,
            "--restart", restart,
            "-p", f"127.0.0.1:{port}:8080",
            "-e", f"LOTSPOT_ADMIN_USER={ADMIN_USER}",
            "-e", f"LOTSPOT_ADMIN_PASSWORD={ADMIN_PASSWORD}",
            "-e", f"LOTSPOT_POS_API_KEY={POS_API_KEY}",
        ]
        if memory:
            args += ["--memory", memory, "--memory-swap", memory]
        if volume:
            # LOTSPOT_DB may only point at /data when /data is actually mounted;
            # see test_container_exits_fast_when_db_directory_is_missing.
            args += ["-v", f"{volume}:/data", "-e", "LOTSPOT_DB=/data/lotspot.db"]
            created["volume"] = volume
        args.append(IMAGE)
        docker(*args)
        created["name"] = name
        created["port"] = port
        base = f"http://127.0.0.1:{port}"
        assert wait_for_http(f"{base}/api/health", timeout=60), "container never healthy"
        return name, base

    try:
        yield _start
    finally:
        if created["name"]:
            subprocess.run(["docker", "rm", "-f", created["name"]], capture_output=True)
        if created["volume"]:
            subprocess.run(["docker", "volume", "rm", created["volume"]], capture_output=True)


# --------------------------------------------------------------------------
# Restart-policy recovery from an ungraceful kill (the OOM signal path)
# --------------------------------------------------------------------------


def test_docker_kill_does_not_trigger_the_restart_policy(chaos_container):
    """Operational gotcha, locked in as a test.

    `--restart unless-stopped` does NOT bring a container back after
    `docker kill`: the daemon treats an explicit CLI kill as an operator stop
    and suppresses the policy. Anyone relying on the restart policy to recover
    from a manual kill will be waiting forever — recovery there needs an
    explicit `docker start`. (A genuine in-kernel OOM is covered separately.)
    """
    name, _base = chaos_container(restart="unless-stopped")
    docker("kill", "--signal", "KILL", name)

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status, _oom, restarts = _container_state(name).split("|")
        if status == "exited":
            break
        time.sleep(0.5)

    status, _oom, restarts = _container_state(name).split("|")
    assert status == "exited", f"expected the container to stay down, got {status!r}"
    assert int(restarts) == 0, (
        f"restart policy fired after an explicit docker kill (RestartCount="
        f"{restarts}); Docker's behaviour changed and the deploy runbook's "
        "recovery steps need revisiting"
    )


def test_committed_sale_survives_ungraceful_kill_and_manual_restart(chaos_container):
    """A sale committed before a SIGKILL must still be there after the operator
    restarts the container — SQLite WAL durability through abrupt process death
    with no graceful shutdown."""
    volume = f"{_TAG}-durvol-{int(time.monotonic() * 1000) % 1_000_000}"
    name, base = chaos_container(restart="unless-stopped", volume=volume)

    headers = _admin_headers(base)
    sku = f"CHAOS-{os.getpid()}"
    created = httpx.post(
        f"{base}/api/products",
        json={"sku": sku, "name": "Durability Cola", "qty": 7, "price_cents": 300},
        headers=headers, timeout=10,
    )
    assert created.status_code == 201, created.text

    # Kill hard — no SIGTERM, no chance to flush or close the DB cleanly.
    docker("kill", "--signal", "KILL", name)
    docker("start", name)
    assert wait_for_http(f"{base}/api/health", timeout=60), "container did not restart"

    catalog = httpx.get(f"{base}/api/products?search={sku}", timeout=10).json()
    survivors = [p for p in catalog["data"] if p["sku"] == sku]
    assert survivors, f"product {sku} did not survive the kill+restart"
    assert survivors[0]["qty"] == 7, f"qty changed across restart: {survivors[0]}"


def test_real_oom_under_memory_limit_is_recorded_and_app_survives(chaos_container):
    """Drive a genuine kernel OOM inside the container's cgroup.

    A memory balloon is allocated until the cgroup limit is hit. The kernel
    kills the offending process — NOT PID 1 — so the container stays up and
    keeps serving, but Docker records State.OOMKilled=true. That flag is the
    only durable evidence the event happened, which is precisely why it is
    worth alerting on: the app looks healthy afterwards.
    """
    name, base = chaos_container(restart="unless-stopped", memory="96m")

    balloon = subprocess.run(
        ["docker", "exec", name, "python", "-c",
         "b=[]\nwhile True: b.append(bytearray(10*1024*1024))"],
        capture_output=True, text=True, timeout=120,
    )
    assert balloon.returncode != 0, "the memory balloon was expected to be killed"

    deadline = time.monotonic() + 20
    oom = "false"
    while time.monotonic() < deadline:
        _status, oom, _restarts = _container_state(name).split("|")
        if oom == "true":
            break
        time.sleep(0.5)

    assert oom == "true", (
        f"no OOM was recorded under a 96m limit; state={_container_state(name)}. "
        "Either the limit was not enforced or the balloon died another way."
    )

    status, _oom, _restarts = _container_state(name).split("|")
    assert status == "running", f"container did not survive the OOM: {status}"
    resp = httpx.get(f"{base}/api/health", timeout=10)
    assert resp.status_code == 200 and resp.json()["success"] is True, (
        f"app stopped serving after the OOM event: {resp.status_code} {resp.text}"
    )


def test_data_persists_across_full_container_recreation(chaos_container):
    """Restart policy aside, a *recreated* container (image redeploy) must keep
    data when the DB is on the mounted volume — this is what preview.env's
    LOTSPOT_DB=/data/lotspot.db buys. Without the volume this data would be
    gone, so this guards the deploy's volume contract."""
    volume = f"{_TAG}-recreate-{int(time.monotonic() * 1000) % 1_000_000}"
    name, base = chaos_container(restart="no", volume=volume)

    headers = _admin_headers(base)
    sku = f"REDEPLOY-{os.getpid()}"
    r = httpx.post(
        f"{base}/api/products",
        json={"sku": sku, "name": "Redeploy Water", "qty": 5, "price_cents": 150},
        headers=headers, timeout=10,
    )
    assert r.status_code == 201, r.text

    # Simulate a redeploy: destroy the container, start a fresh one on the same
    # volume. The fixture only tracks one container, so manage this one inline.
    port = free_port()
    docker("rm", "-f", name)
    new_name = f"{_TAG}-recreated-{int(time.monotonic() * 1000) % 1_000_000}"
    try:
        docker(
            "run", "-d", "--name", new_name,
            "-p", f"127.0.0.1:{port}:8080",
            "-e", f"LOTSPOT_ADMIN_USER={ADMIN_USER}",
            "-e", f"LOTSPOT_ADMIN_PASSWORD={ADMIN_PASSWORD}",
            "-e", f"LOTSPOT_POS_API_KEY={POS_API_KEY}",
            "-e", "LOTSPOT_DB=/data/lotspot.db",
            "-v", f"{volume}:/data",
            IMAGE,
        )
        new_base = f"http://127.0.0.1:{port}"
        assert wait_for_http(f"{new_base}/api/health", timeout=60), "recreated container unhealthy"
        catalog = httpx.get(f"{new_base}/api/products?search={sku}", timeout=10).json()
        survivors = [p for p in catalog["data"] if p["sku"] == sku]
        assert survivors and survivors[0]["qty"] == 5, (
            f"data on the volume was lost across recreation: {catalog['data']}"
        )
    finally:
        subprocess.run(["docker", "rm", "-f", new_name], capture_output=True)


# --------------------------------------------------------------------------
# In-flight request when the container dies
# --------------------------------------------------------------------------


def test_inflight_sse_client_gets_clean_disconnect_not_hang(chaos_container):
    """A client streaming SSE when the container is killed must observe the
    connection drop within a bounded time, not hang until its own timeout.

    The failure mode this guards is a POS terminal frozen on a dead stream.
    """
    name, base = chaos_container(restart="no")

    outcome = {}

    def stream():
        start = time.monotonic()
        try:
            with httpx.Client(timeout=30) as c:
                with c.stream("GET", f"{base}/api/events") as resp:
                    for _line in resp.iter_lines():
                        if time.monotonic() - start > 25:
                            outcome["result"] = "hung"
                            return
        except (httpx.HTTPError, OSError) as exc:
            outcome["result"] = "disconnected"
            outcome["after"] = round(time.monotonic() - start, 2)
            outcome["exc"] = type(exc).__name__
        else:
            outcome["result"] = "closed"
            outcome["after"] = round(time.monotonic() - start, 2)

    thread = threading.Thread(target=stream, daemon=True)
    thread.start()
    time.sleep(2)  # ensure the stream is established and receiving heartbeats

    docker("kill", "--signal", "KILL", name)
    thread.join(timeout=20)

    assert outcome.get("result") in ("disconnected", "closed"), (
        f"SSE client did not notice the container death cleanly: {outcome}"
    )
    assert outcome.get("after", 999) < 20, (
        f"client took {outcome.get('after')}s to notice the disconnect: {outcome}"
    )


def test_container_exits_fast_when_db_directory_is_missing():
    """deploy/preview.env.example ships LOTSPOT_DB=/data/lotspot.db.

    If that env file is used without mounting a volume at /data, the container
    must fail fast and non-zero rather than starting and serving requests
    against a broken database. This locks the deploy contract: *the /data
    volume is mandatory whenever LOTSPOT_DB points into it*.
    """
    if not docker_available():
        pytest.skip("Docker daemon unreachable — cannot run chaos tier.")

    name = f"{_TAG}-nodbdir"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    try:
        docker(
            "run", "-d", "--name", name,
            "-e", f"LOTSPOT_ADMIN_PASSWORD={ADMIN_PASSWORD}",
            "-e", "LOTSPOT_DB=/data/lotspot.db",  # no -v mount on purpose
            IMAGE,
        )
        deadline = time.monotonic() + 30
        state = ""
        while time.monotonic() < deadline:
            state = _container_state(name)
            if state.split("|")[0] == "exited":
                break
            time.sleep(0.5)

        status = state.split("|")[0]
        assert status == "exited", (
            f"container is {status!r} with an unusable LOTSPOT_DB path — it must "
            "exit rather than serve traffic against a broken database"
        )
        exit_code = docker("inspect", "-f", "{{.State.ExitCode}}", name).stdout.strip()
        assert exit_code != "0", f"exited cleanly ({exit_code}); failure must be non-zero"
        logs = docker("logs", name, check=False).stdout + docker("logs", name, check=False).stderr
        assert "unable to open database file" in logs, (
            f"expected a database-open error in the logs, got:\n{logs[-500:]}"
        )
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def test_health_endpoint_is_unreachable_while_container_is_down(chaos_container):
    """Sanity/negative control: with the restart policy off, health must
    actually fail while the container is dead — otherwise every recovery
    assertion above could be passing against a stale success."""
    name, base = chaos_container(restart="no")
    docker("kill", "--signal", "KILL", name)

    # Give the daemon a moment to reap the process, then confirm it is down.
    came_back = wait_for_http(f"{base}/api/health", timeout=5)
    assert not came_back, (
        "health still answered 200 after SIGKILL with restart=no — the kill "
        "did not take, so recovery tests would be vacuous"
    )
