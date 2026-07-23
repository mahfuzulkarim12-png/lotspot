# LotSpot CI/CD Pipeline & Contribution Protocol

This is the source of truth for branch model, commit style, validation, and the
deploy path. It codifies conventions that were previously inferred from
`git log` by each session in turn. **Read this before touching code.**

---

## 1. Branch model

`main` is the base branch and the only long-lived branch. There is currently
**no `origin` remote configured** in the working worktrees — history is local.
Anyone wiring this to a remote should update this section first.

| Branch kind | Pattern | Notes |
|---|---|---|
| Base | `main` | all work branches from and merges back to `main` |
| Agent session | `bohor/<lane>/<mission>-<short8>` | e.g. `bohor/tester/17-7cb5040d` |
| Human feature | `<type>/<short-description>` | e.g. `feat/pos-refunds` |

Rebase onto `main` before starting work and again immediately before
committing. On conflict: `git rebase --abort`, report the conflicting files,
and stop. Do not force-push shared history.

---

## 2. Commit convention

Commits use an **owner-prefixed conventional style**, with no separator between
the owner and the type:

```
Farhan<type>(<scope>): <subject>

<body — what changed and why, specific about endpoints/functions/files>
```

Real examples from history:

```
Farhanfeat(timeclock): add employee clock in/out backend
Farhanfix(admin): fix mobile nav-rail overflow at 375px
Farhantest(backend): add SSE heartbeat and POS oversell concurrency regression tests
Farhanchore(deploy): add preview env template for bohor.com.au deployment
```

**Types:** `feat`, `fix`, `test`, `chore`, `refactor`, `docs`, `perf`, `ci`
**Scopes seen in use:** `backend`, `admin`, `pos`, `timeclock`, `e2e`, `deploy`,
`ci`, `security`, `frontend`

### Hard rules

- **No trailer lines of any kind.** No `Co-Authored-By:`, `Generated-By:`,
  `Assisted-By:`, or any mention of an AI tool, model, or service. The commit
  body ends the message.
- Commits are authored by the repo owner's git credentials.
- No generic subjects (`fix bug`, `update code`). Name the thing that changed.

---

## 3. Test tiers

Four tiers. All must be green before a change is considered done.

| Tier | Location | Runner | Command |
|---|---|---|---|
| Backend unit/integration | `backend/tests/` | pytest | `cd backend && ../venv/bin/python -m pytest` |
| Frontend unit | `frontend/src/**` | vitest | `cd frontend && npm test` |
| E2E (browser) | `e2e-tests/` | Playwright | `cd e2e-tests && npx playwright test` |
| Deploy / ops | `deploy/tests/` | pytest | `cd deploy/tests && ../../venv/bin/python -m pytest` |

### The deploy tier

`deploy/tests/` exercises the **shipped artefacts** rather than the Python app
in-process: the built image, the nginx TLS config, public DNS/TLS for the
preview slug, load, and failure injection. It needs Docker running (colima on
macOS) and, for some tests, network access and the `trivy` / `k6` CLIs.

Markers (see `deploy/tests/pytest.ini`):

| Marker | Meaning |
|---|---|
| `docker` | needs a reachable Docker daemon |
| `network` | reaches the public internet |
| `trivy` | needs the `trivy` CLI |
| `slow` | multi-minute soak / chaos |
| `deploy_gate` | asserts the **public** preview slug is live — see §5 |

Fast local loop (skips soak and the unprovisioned public slug):

```bash
cd deploy/tests
../../venv/bin/python -m pytest -m "not slow and not deploy_gate"
```

---

## 4. Validation order

1. Commit first, then validate — so work survives a hung validator.
2. Run the tier(s) you touched, then the backend suite.
3. Run the full suite twice before declaring green; anything that differs
   between runs is a flaky candidate and must be reported, not re-run until
   it passes.
4. Never weaken an assertion, add a retry, or `xfail`/`skip` a test to reach
   green. A red suite caused by a real defect is a valid deliverable.

Coverage floor: **80% branch coverage on the changed diff**. Produce it
explicitly, e.g.:

```bash
cd backend && ../venv/bin/python -m pytest --cov=. --cov-branch --cov-report=term-missing
```

---

## 5. Deploy path — preview slugs

Preview URLs follow `m<mission_number>-<short8>-preview.bohor.com.au`
(e.g. `m17-7cb5040d-preview.bohor.com.au`).

### What is already provisioned

Verified live by `deploy/tests/test_preview_dns.py`:

- **Wildcard DNS** — `*.bohor.com.au` resolves via Cloudflare. No per-mission
  DNS record needs creating.
- **Wildcard TLS** — the edge presents a Google Trust Services certificate with
  SANs `bohor.com.au` and `*.bohor.com.au`. No per-slug certificate is needed.

### What is missing

There is **no origin binding** for preview slugs, so Cloudflare answers `502`
for every `*-preview.bohor.com.au` hostname. This is the only remaining blocker
and it is an infra action, not a code change:

1. Run the `lotspot-preview` image on a host reachable by Cloudflare.
2. Bind the slug to it (Cloudflare Tunnel, or an origin record + origin cert).
3. Re-run `pytest -m deploy_gate` in `deploy/tests/` — it goes green when the
   slug serves `/api/health` and the SPA.

Until then `deploy_gate` tests fail by design. Deselect them in CI with
`-m "not deploy_gate"`; do not delete or skip them.

### Runtime configuration

Copy `deploy/preview.env.example` to `preview.env` on the deploy host and fill
it in. Never commit the filled-in file.

> **Mandatory:** `preview.env.example` sets `LOTSPOT_DB=/data/lotspot.db`. The
> container **must** be started with a volume mounted at `/data`. Without it the
> process exits 1 at boot with `sqlite3.OperationalError: unable to open
> database file`. Locked by
> `deploy/tests/test_chaos_recovery.py::test_container_exits_fast_when_db_directory_is_missing`.

Run with a **single worker**. Auth tokens and SSE subscribers live in-process;
`uvicorn --workers N` (N > 1) silently breaks both.

### Reverse proxy

`deploy/nginx/preview-tls.conf.template` is the real TLS-terminating config and
is exercised directly by `deploy/tests/test_tls_termination.py`. Two settings
are load-bearing and must not be "tidied away":

- `proxy_buffering off` on `/api/events` — with buffering on, SSE frames are
  withheld and the customer screen never updates.
- `proxy_read_timeout 24h` on `/api/events` — anything below the heartbeat
  interval kills long-lived streams.

---

## 6. Measured capacity

From `deploy/tests/test_load.py` against the container behind the nginx TLS
proxy on a single worker (Apple Silicon, colima):

| Scenario | Load | Result |
|---|---|---|
| Soak | 60 req/s constant, 3 min | ~10,800 requests, **0 errors**, p95 ≈ 9 ms |
| Spike | ramp to 500 VUs, ~70 s | 127,710 requests at ~1,820 req/s, **0 errors**, p95 ≈ 1.1 s, p99 ≈ 2.0 s |

Read that as: the app does not fail under 500-way concurrency, but it does
**queue** — latency degrades roughly 100× between realistic load and
saturation while the error rate stays at zero. For a single store this is
ample headroom; the number to watch if this ever fronts multiple stores is p95
under concurrency, not throughput.

The soak threshold (p95 < 1 s) is a real SLO. The spike threshold (p95 < 3 s)
is a degraded-mode bound for a deliberate saturation test — do not copy it into
the soak. Error-rate and check thresholds are identical in both and must never
be relaxed.

## 7. Operational notes verified by the chaos tier

- **`docker kill` does not trigger the restart policy.** Docker treats an
  explicit CLI kill as an operator stop, so `--restart unless-stopped` will not
  bring the container back. Recovery requires `docker start`.
- **A cgroup OOM does not necessarily stop the container.** Under a memory
  limit the kernel kills the offending process, not PID 1; the app keeps
  serving and looks healthy. The only durable evidence is
  `docker inspect -f '{{.State.OOMKilled}}'` — **alert on that flag**, not on
  container status.
- **SQLite WAL survives SIGKILL.** Data committed immediately before an
  ungraceful kill is intact after restart.

---

## 8. Container image & vulnerability policy

Build: `docker build -t lotspot-preview:latest .`

Policy, enforced by `deploy/tests/test_image_vulnerabilities.py`:

1. **Nothing with an available upstream fix may ship.** Unfixed base-image CVEs
   are outside our control; a fixed-upstream CVE is a choice.
2. **Application runtime dependencies must be clean at every severity** —
   including MEDIUM/LOW/UNKNOWN, which severity-filtered gates hide.
3. **Every finding must appear in `deploy/security/trivy-triage.md`.** A new,
   untriaged CVE fails the build.

Regenerate the triage document after any image or vulnerability-DB change:

```bash
./venv/bin/python deploy/security/generate_triage.py
```

Re-scan periodically — accepted `base-no-fix` findings become *must patch* the
moment Debian publishes a `FixedVersion`.

---

## 9. GitHub Actions

`.github/workflows/e2e-tests.yml` runs the Playwright suite on push/PR to
`main`. The backend, frontend, and deploy tiers are **not** wired into CI yet —
adding them is tracked as follow-up work.
