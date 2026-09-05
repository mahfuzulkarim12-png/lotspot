# LotSpot Build Constitution

**Version:** v0.1
**Status:** Living document

## Preamble

This constitution governs how LotSpot is built, not what it is built with. No
technology stack has been chosen yet, and none of the articles below assume
one. When a stack is chosen, its tooling is expected to implement these
articles — not redefine them.

This is not a PCI compliance policy and does not substitute for one. It is a
craft and security governance document: a small set of invariants that the
team agrees to hold at all times, each backed by a test or a CI gate rather
than by prose alone. An article without an enforcement mechanism is a
proposal, not a rule — see the "Enforced by" line on every article.

### Amendment Process

- This document is amended by pull request only. No article is changed by
  direct commit or by a Slack message becoming folk knowledge.
- Every amending PR must state, in its description, which article(s) it
  changes and why.
- Adding or strengthening an article requires the PR to also add or update
  the enforcing test/CI gate in the same PR, or to explicitly mark the new
  article `Enforced by: TBD` if the gate is follow-up work.
- Weakening or removing an article requires explicit justification in the PR
  description; it cannot be done silently as a side effect of an unrelated
  change.
- This document is versioned with semver, independent of the application's
  own version:
  - **MAJOR** — an article is removed or weakened.
  - **MINOR** — a new article is added.
  - **PATCH** — wording/clarification changes that do not alter what is
    required or how it is enforced.

---

## Article 1 — No PAN, No CVV, No Track Data

LotSpot never stores, processes, logs, or transmits a cardholder's Primary
Account Number (PAN), CVV/CVV2, or magnetic-stripe/chip track data, in any
form, at any layer (database, logs, fixtures, broadcasts, backups). Card
acceptance is delegated to a P2PE-validated or semi-integrated payment
terminal; LotSpot's application code only ever receives and stores a
post-authorization approval token, the card's last 4 digits, and an
authorization code.

**Rationale:** the cheapest way to fail PCI scope, leak cardholder data, or
create a breach liability is to let a PAN transit or rest in application
code that was never designed to protect it. Keeping the full PAN off every
LotSpot system by construction removes an entire class of risk regardless of
what stack is chosen.

**Enforced by:** TBD — gate to be added. Proposed gate: a CI step that runs a
regex scan (PAN-shaped patterns, e.g. 13–19 digit sequences passing a Luhn
check; CVV-labeled fields; track 1/2 patterns) over source, logs, test
fixtures, and any recorded broadcasts, failing the build on a match outside
an explicit allowlist (e.g. clearly-fake test PANs marked as such).

---

## Article 2 — Money Is Integer Cents, Never Floats

All monetary amounts are represented as integer cents end to end — in the
database, in the API, and in the client. Floating-point representations of
money are not used anywhere in the codebase.

**Rationale:** floating-point arithmetic on currency silently accumulates
rounding error (e.g. `0.1 + 0.2 !== 0.3`), which in a POS system means drift
between what was charged, what was recorded, and what reconciles at
close-out. Integer cents make every arithmetic operation exact.

**Enforced by:** `backend/db.py` — every money-bearing column is declared as
an `INTEGER` cents column, e.g. `price_cents` (`backend/db.py:31`),
`unit_price_cents` and `total_cents` (`backend/db.py:46-47`), and `tax_cents`
(`backend/db.py:108`). On the client, `frontend/src/lib/money.js:1` states
the same invariant explicitly ("All API money values are integer cents;
these are the only two conversions") and `frontend/src/lib/money.js:6-27`
implement cents-only rounding and parsing rather than float math.

---

## Article 3 — Every Persisted Row Carries store_id, and Every Timestamp Carries Both Local and UTC

Every row that represents business activity (sales, products, employees,
time entries) is stamped with a `store_id` at write time, defaulting to the
current store rather than being left null or blank. Every "when did this
happen" timestamp is recorded twice: once in local time for the on-floor
operator experience, and once in UTC for cross-store aggregation.

**Rationale:** LotSpot's data model anticipates multiple stores and a future
HQ sync before either exists. Retrofitting a tenant key or a UTC timestamp
onto historical rows after the fact is far more expensive than stamping it
from day one, and migrations that backfill defaults for existing rows prove
the discipline was followed even before the column existed.

**Enforced by:** `backend/db.py` — `store_id TEXT NOT NULL DEFAULT ''` on
`products` (`backend/db.py:32`), `sales` (`backend/db.py:57`), `employees`
(`backend/db.py:128`), and `time_entries` (`backend/db.py:137`), each with a
corresponding idempotent `ALTER TABLE ... ADD COLUMN` + backfill migration
(e.g. `backend/db.py:196-200` for `sales.store_id`). The dual-timestamp
requirement is enforced by `sales.sold_at` (`backend/db.py:53`) alongside
`sales.sold_at_utc` (`backend/db.py:56`, added via migration at
`backend/db.py:202-204`), and covered by
`backend/tests/test_migrations.py` plus the dedicated edge-case suite
referenced in the `9e1a0e5` commit ("cover store_id/sold_at_utc HQ-sync edge
cases").

---

## Article 4 — Every State Mutation Passes Through require_admin

Every endpoint that creates, updates, deletes, or otherwise mutates
persisted state requires an authenticated admin, enforced via the
`require_admin` dependency (or its equivalent in whatever framework is
eventually chosen). There is no mutating endpoint that is reachable without
authentication "for now" or "just for internal use."

**Rationale:** a POS system's entire attack surface is its write path — sales,
voids, inventory adjustments, tax configuration, employee records. An
unauthenticated or under-authenticated mutation endpoint is a direct path to
inventory fraud, revenue manipulation, or data corruption.

**Enforced by:** `backend/app.py:88` defines `require_admin`; every
state-mutating route declares it as a FastAPI dependency, e.g.
`create_sale` (`backend/app.py:866`), `pos_checkout` (`backend/app.py:881`),
`create_product` / `update_product` / `delete_product`
(`backend/app.py:556,594,627`), the tax account and tax category CRUD
endpoints (`backend/app.py:655-802`), void-sale endpoints
(`backend/app.py:974,1007`), and employee/timeclock endpoints
(`backend/app.py:1141-1206`).

---

## Article 5 — Realtime Broadcasts Never Carry Sensitive Fields

Server-sent events and any other realtime broadcast channel strip sensitive
fields before the payload leaves the server. Fields such as the identity of
the cashier who rang a sale, who voided it, and why, are never present in a
broadcast payload, even though they are legitimately present in the
authenticated REST response for the same record.

**Rationale:** a broadcast channel is a different trust boundary than an
authenticated request-response endpoint — it is easy to add a new field to a
response model and forget that the same object is also serialized onto an
SSE stream with a different (or no) access check. Explicitly enumerating and
stripping sensitive fields at the broadcast boundary makes that failure mode
structural rather than something reviewers must remember to check.

**Enforced by:** `_SENSITIVE_BROADCAST_FIELDS = ("cashier", "voided_by",
"void_reason")` at `backend/app.py:53`, applied when building the broadcast
payload at `backend/app.py:66`. Verified by
`backend/tests/test_events.py:232-249`
(`test_sse_does_not_broadcast_sensitive_sale_fields`) and the companion void
test immediately following it, both of which subscribe to the live event
broadcaster and assert the sensitive keys are absent from every emitted
`sale` event.

---

## Article 6 — Schema Migrations Are Tested and Idempotent

Every schema change ships as an idempotent migration: it can be run against
a fresh database, run again with no effect, and run against a
pre-existing/legacy database to bring it up to date without data loss. Every
migration has a test that exercises it against a simulated legacy schema.

**Rationale:** a POS system holds a live, append-heavy database in
production; a migration that isn't idempotent or that assumes a fresh schema
will eventually be run against real data at the worst possible time (an
in-place upgrade during business hours). Testing migrations against a
legacy fixture is the only way to catch that before it happens for real.

**Enforced by:** every table definition in the static schema uses
`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` (e.g.
`backend/db.py:26,37,66,74,84,93,102,112,114,124`), and every subsequent
column addition is guarded by a column-existence check before the
`ALTER TABLE` runs (e.g. `backend/db.py:192-204,246-272`). Correctness is
verified by `backend/tests/test_migrations.py`, which constructs a
`LEGACY_SCHEMA` (pre-tax, pre-`store_id`) database and asserts the migration
path upgrades it cleanly and leaves historical data unchanged.

---

## Article 7 — Append-Only Audit Log for Privileged Actions

Every admin/privileged action — tax configuration edits, product and
employee CRUD, sale voids, and admin logins/logouts — is recorded to an
append-only audit log capturing who performed the action, what it was, what
changed, and when. Audit log rows are never updated or deleted by
application code.

**Rationale:** the same `require_admin` gate that authorizes an action
(Article 4) also means a compromised or careless admin credential can make
those changes invisibly. An append-only trail is what lets an operator
reconstruct "what happened to my inventory/tax settings/employee records"
after the fact, and is a baseline expectation for any system that touches
money and staff records.

**Enforced by:** TBD — gate to be added. Proposed gate: an audit-log table
(or table-agnostic append-only store) written to by every route currently
protected by `require_admin` in `backend/app.py`, plus a CI test that
asserts (a) each privileged mutation produces exactly one audit row, and
(b) no application code path issues an `UPDATE` or `DELETE` against the
audit table.

---

## Article 8 — Secrets Are Sourced From the Environment Only

No secret, API key, credential, or token is committed to source, checked
into a fixture, or hardcoded anywhere in the repository. All secrets are
read from the environment (or a secret manager) at runtime, and the
application fails fast at startup if a required secret is missing.

**Rationale:** secrets in source or fixtures survive in git history even
after being "removed," and get silently reused across dev/test/prod when
no one had to think about where they came from. Sourcing from the
environment only, with a fail-fast startup check, makes their absence loud
instead of silently falling back to an insecure default.

**Enforced by:** TBD — gate to be added. Proposed gate: a CI secret-scanning
step (e.g. regex/entropy scan) run over the diff on every PR, plus a startup
check in the application that raises immediately if a declared-required
secret environment variable is unset, with a test asserting that startup
fails when it is missing.
