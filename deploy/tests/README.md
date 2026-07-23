# Deploy / ops test tier

Tests the **shipped artefacts**, not the Python app in-process:

| File | Covers |
|---|---|
| `test_preview_dns.py` | public DNS + TLS for `*-preview.bohor.com.au`, and whether the slug actually serves the app |
| `test_image_vulnerabilities.py` | trivy posture of `lotspot-preview:latest`, triage completeness |
| `test_tls_termination.py` | `deploy/nginx/preview-tls.conf.template` — cert, protocol floor, redirects, headers, SSE through TLS |
| `test_proxy_concurrency.py` | oversell race + concurrent load **through** the proxy |
| `test_chaos_recovery.py` | SIGKILL, real cgroup OOM, restart/recreation durability |
| `test_load.py` + `k6/load.js` | multi-minute soak and high-concurrency spike |

`backend/tests/` still owns the application's own behaviour; nothing here
duplicates it.

## Prerequisites

- Docker daemon running (`colima start` on macOS)
- The image built: `docker build -t lotspot-preview:latest .` from the repo root
- `trivy` and `k6` on PATH (`brew install trivy k6`) for their respective tiers
- Network access for `test_preview_dns.py`

## Running

```bash
cd deploy/tests

# Fast loop — skips soak/chaos and the not-yet-provisioned public slug
../../venv/bin/python -m pytest -m "not slow and not deploy_gate"

# Everything except the public-slug gate
../../venv/bin/python -m pytest -m "not deploy_gate"

# Just the public preview slug (currently RED — see below)
../../venv/bin/python -m pytest -m deploy_gate
```

Useful environment overrides:

| Variable | Default | Purpose |
|---|---|---|
| `LOTSPOT_TEST_IMAGE` | `lotspot-preview:latest` | image under test |
| `LOTSPOT_PREVIEW_SLUG` | `m17-7cb5040d-preview.bohor.com.au` | slug to probe |
| `LOTSPOT_TRIVY_JSON` | — | reuse an existing trivy report instead of rescanning |
| `LOTSPOT_NGINX_TEMPLATE` | `deploy/nginx/preview-tls.conf.template` | point at a variant config (used for negative controls) |
| `LOTSPOT_SOAK_DURATION` | `3m` | soak length; raise for a real overnight soak |
| `LOTSPOT_SPIKE_VUS` | `500` | peak concurrency for the spike scenario |

## Expected failures

`-m deploy_gate` fails on purpose. DNS and TLS for `*.bohor.com.au` are
provisioned, but no origin is bound to preview slugs, so Cloudflare returns
`502`. Those tests go green once someone runs the image behind the slug — see
§5 of [`docs/CI_CD_PIPELINE.md`](../../docs/CI_CD_PIPELINE.md). Do not skip or
delete them to get a green board.
