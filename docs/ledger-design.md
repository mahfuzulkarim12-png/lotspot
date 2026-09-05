# LotSpot Ledger Design Spike (M0)

Status: design only — no code under `backend/` is touched by this mission.
Feeds: M1 (accounts + posting engine), M2 (payments/idempotency), M3 (void/refund).

## 0. Grounding in current code

- `backend/db.py` `SCHEMA` + `init_db()`: static `CREATE TABLE IF NOT EXISTS` script
  runs first, then `_migrate_*` functions run `ALTER TABLE` + follow-up
  `CREATE INDEX`. Any new table this spike proposes (`ledger_entries`, `payments`,
  `idempotency_keys`) is a **new** table, so it can go straight into `SCHEMA` like
  `audit_log` did — no migration function needed for the initial add. Any *later*
  column added to an existing table (e.g. a `ledger_entry_id` FK bolted onto
  `sales`) must follow the `_migrate_sales_table` pattern: `ALTER TABLE` first,
  matching `CREATE INDEX` only inside that migration function, never in `SCHEMA`
  (`backend/db.py:66-70` documents why — index-before-column raises
  `sqlite3.OperationalError` on upgrade).
- `backend/app.py` `_insert_sale_row` (`backend/app.py:247-317`) is the single
  insert chokepoint for both `_record_sale` (manual/POS-terminal single-item
  sale) and `_record_checkout` (cart checkout). It already computes
  `total_cents` (tax-exclusive) and `tax_cents` per line, and the checkout
  aggregate returns `grand_total_cents = subtotal_cents + tax_cents`. Ledger
  posting for M1 should hook into this same chokepoint (or immediately after
  `conn.commit()` in `_record_sale`/`_record_checkout`) so every sale path is
  posted identically, mirroring how tax computation was centralized there.
- `payment_method` today is a free-text label ("cash"/"card") validated only
  by `models.py:85` (`min_length=1, max_length=PAYMENT_METHOD_MAX`) — no
  enum, no linkage to a payments/processor record. This spike's `payments`
  table (§3) is the missing link.
- The already-completed **PCI-DSS v4.0.1 gap register**
  (`docs/pci-dss-gap-register.md`) already commits LotSpot to a
  semi-integrated/P2PE terminal model: "LotSpot itself never receives,
  transmits, processes, or stores cardholder data (CHD/PAN)"
  (`docs/pci-dss-gap-register.md:11-13`), confirmed by `payment_method` being
  a label only (`docs/pci-dss-gap-register.md:54-55`). This ledger design
  must not reopen that boundary — see §6.
- Current void endpoints (`void_sale` / `void_receipt`, `backend/app.py:1021-1088`)
  only set `voided_at`/`voided_by`/`void_reason` on `sales` rows. They do
  **not** restore `products.qty` and do **not** touch any ledger — this is a
  real gap M3 must close (see §5).
- `backend/models.py:20-25` `ProductIn` has `sku`, `name`, `qty`,
  `price_cents`, `tax_category_id` — **no cost/cost_basis field of any kind**.
  This is the reason COGS/inventory-asset postings are out of scope for v1
  (see §1).

## 1. Chart of accounts (v1)

| Code | Account | Type | Normal balance | Populated by |
|------|---------|------|-----------------|--------------|
| 1000 | Cash on Hand | Asset | Debit | Cash tender sales |
| 1100 | Card Clearing | Asset (in-transit) | Debit | Card tender sales, cleared on settlement |
| 2100 | Tax Payable | Liability | Credit | Tax portion of every sale |
| 4000 | Sales Revenue | Revenue | Credit | Tax-exclusive subtotal of every sale |

**Explicitly DEFERRED — do not implement in v1:**

- **1500 Inventory (asset)** — deferred.
- **5000 COGS (expense)** — deferred.

**Why:** `ProductIn` (`backend/models.py:20-25`) carries `price_cents` (sale
price) but no `cost_cents` / cost-basis field anywhere in the schema or
models. Without a cost basis there is nothing to relieve from an inventory
asset account or expense as COGS — any COGS/inventory posting would have to
be fabricated (e.g. assume cost = 0, or = price, both wrong) or would require
a schema change that is out of scope for this spike. **v1 posts the revenue
side of the sale only** (cash/card in, revenue + tax out). Adding a cost
basis to products and turning on COGS/inventory-asset postings is an
explicit future mission, not part of M1–M3.

## 2. Per-tender double-entry posting table

One posting per sale (or per checkout transaction, aggregated across its line
items — `_record_checkout` already aggregates `subtotal_cents`/`tax_cents`
across merged cart lines at `backend/app.py:437-449`). Amounts below use the
existing field names from `_insert_sale_row`/`_record_checkout`.

### Cash tender

| Account | Debit | Credit |
|---|---|---|
| 1000 Cash on Hand | `grand_total_cents` | |
| 4000 Sales Revenue | | `subtotal_cents` |
| 2100 Tax Payable | | `tax_cents` |

Balance check: `grand_total_cents == subtotal_cents + tax_cents` — already
guaranteed by the existing computation at `backend/app.py:316` and
`backend/app.py:449`, so debits == credits falls out for free.

### Card tender

| Account | Debit | Credit |
|---|---|---|
| 1100 Card Clearing | `grand_total_cents` | |
| 4000 Sales Revenue | | `subtotal_cents` |
| 2100 Tax Payable | | `tax_cents` |

Identical shape to cash, only the debit leg's account differs (1100 instead
of 1000). This symmetry is intentional: the posting function should take
`payment_method` and look up the debit account (`cash` → 1000, `card` →
1100) rather than branching on separate code paths.

### Sketch: `ledger_entries` DDL (for M1)

```sql
CREATE TABLE IF NOT EXISTS ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL,       -- groups the balanced entry set (matches sales.transaction_id)
    account_code TEXT NOT NULL,         -- '1000', '1100', '2100', '4000'
    direction TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    store_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_transaction_id ON ledger_entries (transaction_id);
```

M1 posting invariant to enforce in application code (mirroring the
`audit_log` "app code must only ever INSERT" comment at `backend/db.py:149-150`):
for a given `transaction_id`, `SUM(amount_cents WHERE direction='debit') ==
SUM(amount_cents WHERE direction='credit')`. This should be asserted in the
posting function immediately before `conn.commit()`, not just trusted.

## 3. Semi-integrated card model

LotSpot **records** what an external, PCI-validated card terminal reports
back (reference, auth code, status) and posts that to Card Clearing (1100).
It **never** handles PAN, never embeds a processor SDK, and never becomes a
control point that processes cardholder data. This is not a new decision —
it is the same boundary the PCI gap register already drew
(`docs/pci-dss-gap-register.md:11-20`): SAQ P2PE/A-EP eligibility depends on
LotSpot staying out of the CDE, which requires it to only ever see
tokens/references, never PAN/track/CVV.

### Sketch: `payments` DDL (for M2)

```sql
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL,       -- matches sales.transaction_id for this receipt
    tender_type TEXT NOT NULL CHECK (tender_type IN ('cash', 'card')),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    -- Card-only fields below; NULL for cash. All three are terminal-reported
    -- references, never PAN/track/CVV — enforce this at the Pydantic layer.
    processor_ref TEXT,      -- terminal/gateway transaction reference
    auth_code TEXT,          -- authorization code returned by the terminal
    status TEXT,             -- e.g. 'approved', 'declined', 'voided'
    store_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_transaction_id ON payments (transaction_id);
```

`payments` is additive metadata alongside the ledger posting in §2 — the
ledger entry for a card sale posts to 1100 regardless of what `payments`
records; `payments` is the audit trail of *what the terminal said*, useful
for reconciling Card Clearing against the processor's settlement batch
later. Any field here that could carry PAN (a raw terminal payload dump, for
example) must be rejected at the API boundary — this is exactly the standing
constraint the PCI gap register already flags for Req 3
(`docs/pci-dss-gap-register.md:77`): "any change that pipes raw
terminal/gateway responses into LotSpot's DB or logs must be reviewed to
confirm no PAN leaks through."

## 4. Idempotency

`sales.transaction_id` (`backend/db.py:40`) is **not** a candidate idempotency
key: it groups the line items of one checkout (one `transaction_id` → many
`sales` rows, one per merged cart line — see `_record_checkout`,
`backend/app.py:361-453`), and it is not declared `UNIQUE` in the schema. A
retried checkout request with the same idempotency key must map to the
*same* transaction_id and return the *same* response, not create a second
set of `sales` rows or a second ledger posting.

### Sketch: `idempotency_keys` DDL (for M2)

```sql
CREATE TABLE IF NOT EXISTS idempotency_keys (
    store_id TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    response_json TEXT NOT NULL,        -- cached response body to replay verbatim
    created_at TEXT NOT NULL,
    PRIMARY KEY (store_id, key)
);
```

Usage pattern for M2's checkout endpoint: caller supplies an idempotency key
(e.g. `Idempotency-Key` header, generated client-side per checkout attempt).
On request: look up `(store_id, key)`; if found, replay `response_json`
as-is (no re-insert, no re-post). If not found, run `_record_checkout` +
ledger posting inside the same transaction that commits, then insert the
`(store_id, key) → (transaction_id, response_json)` row before returning.
The `PRIMARY KEY (store_id, key)` uniqueness is what actually enforces
"at most once" — it is not derived from anything on `sales`.

## 5. Void vs refund accounting rule

- **Void = never-sold unwind.** The sale is cancelled before it is treated as
  a completed, external-facing transaction (e.g. cashier error caught
  immediately, or same-session correction). Accounting effect: **reverse the
  original ledger postings in full** (debit/credit legs flipped) and
  **restore `products.qty`**. Net effect on all four accounts is zero — as if
  the sale never happened. This matches the plain-English meaning of "void"
  already used in `void_sale`/`void_receipt` naming, but today's
  implementation (`backend/app.py:1021-1088`) only flags the row — it does
  **not** restore stock or touch any ledger, which is a real gap relative to
  this accounting rule.
- **Refund = goods returned after a completed sale.** The original sale's
  postings stand (it happened; revenue was recognized). A refund is a new,
  independent transaction that posts the reverse legs (Dr Sales Revenue, Dr
  Tax Payable, Cr Cash on Hand/Card Clearing) and optionally restores stock
  only if the goods are physically returned in sellable condition — that
  restock decision is a business rule, not an accounting one, and should be
  a separate flag from the refund itself.
- **Recommendation for M3:** implement **void** as "reverse ledger postings +
  restore stock," wired into the existing `void_sale`/`void_receipt`
  endpoints (extending them, not replacing them). Refund is a distinct,
  new-transaction flow and is **not** required to ship in M3 — flag it as a
  follow-on mission once void/ledger reversal is proven, since refund
  additionally needs a "was this restocked?" decision that void does not.

## 6. Card-acceptance trade-off — flag for @ongar

Farhan's parenthetical describing a "portal that authenticates and debits
customers' accounts," if implemented literally, means LotSpot would
authenticate cardholders and initiate debits itself — i.e., **LotSpot
becomes the payment processor/gateway**, receiving and acting on PAN/account
credentials directly. That is a fundamentally different system than what
exists today and than what the PCI gap register scoped
(`docs/pci-dss-gap-register.md:11-30`, "Rejected alternative: LotSpot
processes/stores/transmits PAN directly"): it would blow LotSpot out of
SAQ P2PE/A-EP eligibility and into **SAQ D** territory — full PCI-DSS
applicability with a Report on Compliance, QSA assessment, and ongoing
compliance costs that are out of proportion to a single-store POS.

**Recommendation:** keep the record-only, semi-integrated model described in
§3 — LotSpot never sees PAN, never authenticates cards, never debits
accounts; a certified terminal (or P2PE/A-EP-eligible gateway) does that, and
LotSpot only records the terminal's reference/auth/status result and posts
the settlement amount to Card Clearing. **This needs an explicit decision
from @ongar** before M2 builds anything: confirm the record-only model is
what's wanted, since it means LotSpot's UI will never itself prompt for card
number/expiry/CVV under any circumstance.

## Summary for M1–M3

- **M1**: implement `ledger_entries` (§2 DDL) + a posting function hooked
  into `_insert_sale_row`/`_record_checkout`'s commit path (§0); no
  COGS/inventory postings (§1).
- **M2**: implement `payments` (§3 DDL) + `idempotency_keys` (§4 DDL) for the
  checkout endpoint; card fields are references only, never PAN (§3, §6).
- **M3**: implement void as reverse-postings + stock restore (§5); refund is
  a follow-on, not required for M3.
- **Human decision needed before M2**: @ongar to confirm record-only
  semi-integrated card model (§6) before any processor/gateway integration
  work starts.
