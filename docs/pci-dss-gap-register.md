# PCI-DSS v4.0.1 Gap Register — LotSpot

**Standard version:** PCI-DSS v4.0.1 (published June 2024). v3.2.1 was formally
retired by the PCI Security Standards Council on 31 March 2024 and MUST NOT be
used as the compliance baseline going forward. Source: PCI SSC, "PCI DSS v4.0.1
Resource Hub," pcisecuritystandards.org/document_library (retrieved 2026-09-05).
This register is written entirely against v4.0.1 requirement numbering.

## Scope decision (read this first)

**Recommended acceptance architecture: semi-integrated, P2PE-validated (or
equivalent) terminal. LotSpot itself never receives, transmits, processes, or
stores cardholder data (CHD/PAN).** Card payment is handled entirely by a
standalone or semi-integrated payment terminal that encrypts card data at the
point of interaction and only returns a tokenized/masked result (approval code,
last-4, token) to LotSpot over the local integration channel. Under this model
LotSpot's own PCI eligibility falls under **SAQ P2PE** (if using a PCI SSC
P2PE-listed solution end-to-end) or **SAQ A-EP** (if the terminal is
semi-integrated but not P2PE-listed and LotSpot's page/app is a control point
in how the terminal is invoked, e.g. redirect/API-triggered card entry).
Exact SAQ falls out of the final terminal integration once selected — not
decided in this document — but both tracks share the defining property that
**LotSpot's codebase, database, and network segment are out of CDE (cardholder
data environment) scope** for storage/transmission because no CHD ever touches
them.

**Rejected alternative: LotSpot processes/stores/transmits PAN directly (SAQ D
/ full merchant Report on Compliance).** This would require:
- A Qualified Security Assessor (QSA) engagement and annual on-site/remote RoC
  (Req 12.4, PCI SSC RoC Reporting Template) rather than a self-administered SAQ.
- A formally segmented CDE (network segmentation testing per Req 11.4.5,
  firewall/router rule review per Req 1) — LotSpot currently ships as a
  single-process FastAPI app with SQLite on the POS machine itself
  (`backend/db.py:3-6`), with no network segmentation model at all.
- A documented key-management program for cryptographic keys protecting stored
  PAN (Req 3.6, 3.7) — LotSpot has no cryptographic key management subsystem;
  `auth.py` only manages password hashing salts, not payment key material.
- Quarterly external vulnerability scans by an Approved Scanning Vendor (Req
  11.3.2) and a formal penetration-testing program (Req 11.4).
- Ongoing PAN-at-rest protection (Req 3.3–3.5: truncation, strong crypto,
  keyed hashes) applied to a `sales.payment_method` column and any payment
  audit trail — a significant, continuously-audited liability for a small POS
  vendor with no dedicated security team.

This is **not recommended**: it multiplies audit cost and ongoing compliance
burden (QSA fees, ASV scans, segmentation testing, key rotation) for a small
retail POS product, when the same payment functionality is achievable by
having a certified terminal absorb 100% of the CHD handling. Every gap-register
row below therefore marks "in-scope under recommended tier" based on the
P2PE/A-EP scope boundary, not the SAQ D boundary.

## Current state: LotSpot is out of PCI scope today

There is no cardholder data anywhere in the current codebase. `payment_method`
is a free-text label only ("cash"/"card"), not PAN, expiry, or CVV:
- `backend/app.py:254` — `_insert_sale_row(..., payment_method: str | None, ...)`
  — the field is typed and used purely as a label passed straight through to
  the `sales` row (`backend/app.py:272,286,441`).
- `backend/db.py:37-64` — `sales` table schema: `payment_method TEXT` column,
  no PAN/track-data/CVV/expiry columns exist anywhere in `SCHEMA`.

This means: **today, PCI-DSS does not apply to LotSpot at all**, because no
requirement in DSS is triggered without CHD/SAD present. This register
analyzes the gaps that will become live obligations the moment a payment
terminal integration is added under the recommended P2PE/A-EP tier, plus the
handful of Req 8/10 gaps that are good practice regardless of card scope
because LotSpot already handles PII-adjacent business data (sales, employees).

---

## Requirement-by-requirement gap table

| Req | Title (v4.0.1) | LotSpot current status | Gap | In-scope under recommended tier (P2PE/A-EP)? |
|---|---|---|---|---|
| **1** | Install and maintain network security controls | LotSpot runs as a single FastAPI process behind nginx TLS termination (`deploy/nginx/preview-tls.conf.template`); no firewall/NSC policy, no network diagram, no segmentation between POS app and any other network segment exists in the repo. | No documented network security control (NSC) ruleset or segmentation test. | **No** — under P2PE/A-EP, Req 1's CDE-segmentation burden sits on the terminal vendor's validated P2PE solution or on the merchant's payment network, not on the LotSpot host, *provided* LotSpot's network segment is demonstrated not to have access to the terminal's CHD-carrying interface. Still recommend basic NSC hygiene (host firewall, only necessary ports open) as general security practice, not a DSS-driven requirement. |
| **2** | Apply secure configurations to all system components | Admin account is seeded with a hardcoded default (`admin`/`admin`) unless `LOTSPOT_ADMIN_PASSWORD` is set (`backend/auth.py:82-91`); no evidence of a hardening checklist, disabled default services inventory, or config-baseline doc. | Default credentials shipped as fallback violates "no vendor default accounts/passwords" baseline. | **Yes, partially** — Req 2.3.1 (change vendor defaults) applies to any system in or connected to the CDE; even out-of-CDE hosts should not ship default admin creds as a general hardening matter. Track under Req 8 remediation below (same root cause). |
| **3** | Protect stored account data | No account data (PAN/SAD) is stored anywhere in the schema — verified: `backend/db.py:37-64` `sales` table has no PAN/track/CVV columns; `payment_method` is a label only (`backend/app.py:254`). | None today. If a future feature ever logs a raw terminal response containing PAN (e.g., debug logging of a gateway payload), that would immediately create Req 3 exposure. | **No** — by design under P2PE/A-EP, LotSpot never receives PAN so Req 3 storage rules don't attach to it. Flag as a standing constraint: any change that pipes raw terminal/gateway responses into LotSpot's DB or logs must be reviewed to confirm no PAN leaks through. |
| **4** | Protect cardholder data with strong cryptography during transmission | TLS terminates at nginx (`deploy/nginx/preview-tls.conf.template`); no CHD ever transits LotSpot's HTTP surface under the recommended tier (terminal talks to processor directly or via its own encrypted channel). | Not verified: TLS cipher/version policy in the nginx template (needs check against Req 4.2.1's disallowed-protocol list — no SSL/early TLS). | **Partially** — Req 4 applies to the transmission of CHD over open/public networks; if the terminal-to-LotSpot link only carries tokens/masked data (P2PE/A-EP model), Req 4 doesn't attach to that link, but LotSpot's own TLS config remains general good practice and should still be checked against the current TLS 1.2+ minimum. |
| **5** | Protect all systems and networks from malicious software | No AV/anti-malware or file-integrity tooling referenced anywhere in `backend/`, `deploy/`, or CI config found in this repo. | No malware-protection program. | **No** — Req 5 is scoped to CDE systems; LotSpot's own host is out of CDE under the recommended tier. Not a blocking gap for this scope decision. |
| **6** | Develop and maintain secure systems and software | No dependency-vulnerability scanning, no documented SDLC/security-review gate found in `docs/CI_CD_PIPELINE.md` beyond general CI; `backend/requirements.txt` pins versions but no scheduled CVE scan job located. | No formal secure-SDLC or patch-management cadence. | **Yes, narrowly** — Req 6.3 (vulnerability management) and 6.4 (public-facing app protections, e.g. WAF/code review for injection) apply if LotSpot's web app is a control point that triggers or displays terminal payment flows (the A-EP condition). Recommend adding dependency scanning (`pip-audit`/`safety`) as a low-cost mitigation regardless of final SAQ. |
| **7** | Restrict access to system components and cardholder data by business need to know | Single shared `admin` account/role — no role separation, no least-privilege model; every authenticated caller has full admin rights (`backend/auth.py` has one `admin_users` table, no roles/permissions concept found). | No need-to-know access model; anyone with the shared admin password has full access to sales, employee, and tax data. | **Yes** — even without CHD, Req 7's least-privilege principle is the direct analog of a real operational risk here: no distinction between a cashier role and an owner/admin role. Recommend before scaling past a single operator. |
| **8** | Identify users and authenticate access to system components | Strong password hashing: PBKDF2-HMAC-SHA256, 600,000 iterations, per-user random salt (`backend/auth.py:17-29`, `hash_password`/`verify_password`). **But:** (a) single shared `admin` login only, no per-user identity beyond a single row unless multiple `admin_users` rows are manually created — the seed path creates exactly one (`backend/auth.py:72-97`); (b) default password `admin`/`admin` is seeded whenever `LOTSPOT_ADMIN_PASSWORD` is unset, with only a log warning (`backend/auth.py:20-21, 82-91`); (c) no MFA anywhere in `auth.py`; (d) no login rate-limiting or lockout after failed attempts (no such logic found in `backend/app.py` or `backend/auth.py`); (e) 12-hour token TTL with no idle/inactivity timeout — `TOKEN_TTL_HOURS = 12` (`backend/auth.py:18`), `TokenStore.validate` only checks absolute expiry, never last-use time (`backend/auth.py:58-66`); (f) tokens are held in an in-memory dict (`TokenStore.__init__`, `backend/auth.py:45-56`) with no persistence, revocation list, or audit of concurrent sessions. | Multiple Req 8 sub-requirements unmet: 8.2.2 (shared/generic accounts prohibited for CDE-adjacent access), 8.3.6 (password complexity/default-credential elimination), 8.4.2 (MFA for all access into the CDE — applies to A-EP integration points), 8.2.8/8.6.1 (idle session timeout ≤15 min for admin sessions touching in-scope systems), no brute-force lockout (8.3.4 equivalent intent). | **Yes** — Req 8 is the sharpest gap even under the reduced P2PE/A-EP tier, because LotSpot's admin session is the control point that can trigger/display the terminal's payment flow (A-EP condition) and definitely governs access to sales/financial records. Concrete remediation: per-user accounts + roles, forced default-password rotation, MFA on admin login, failed-login lockout, and an idle-timeout ≤15 minutes on top of the 12h absolute TTL. |
| **9** | Restrict physical access to cardholder data | Physical security of the POS machine/terminal is entirely a merchant/site-operational control, not something LotSpot's codebase can enforce. | No physical-security documentation exists in this repo (expected — out of software scope). | **Yes, but not code-addressable** — applies to the physical terminal and any media storing CHD; the terminal vendor's P2PE validation covers device tamper-detection. Nothing to remediate in LotSpot's codebase; flag for the operator's site-security policy doc. |
| **10** | Log and monitor all access to system components and cardholder data | Only the `sales` table carries a partial audit trail: `cashier`, `voided_at`, `void_reason`, `voided_by` columns exist for sale/void actions (`backend/db.py:37-64`). **There is no general audit-log table.** Admin actions outside of sales — tax-rate edits, product CRUD, employee CRUD, login events, permission changes — are not logged anywhere; a grep of the backend for a generic audit/event log table found none. | No centralized, tamper-evident audit log covering all administrative access and configuration changes, as Req 10.2 requires (all individual user access to CHD, all actions by privileged accounts, all changes to identification/authentication mechanisms, all initialization/stopping/pausing of audit logs). | **Yes** — this is the second sharpest gap. Even scoped down to P2PE/A-EP, Req 10.2.1 requires logging of all actions taken by any individual with administrative privileges, which today has zero coverage outside the sales/void trail. Recommend a dedicated `audit_log` table capturing actor, action, target, before/after values, and timestamp for every admin-privileged write, plus login success/failure events. |
| **11** | Test security of systems and networks regularly | No ASV scan records, no penetration-test reports, no vulnerability-scanning tooling/config found anywhere in the repo. | No regular external vulnerability scanning or segmentation testing. | **Partially** — Req 11.3.2 (quarterly ASV scans) applies to internet-facing systems in scope; if LotSpot's admin UI is internet-reachable and is the A-EP control point, it needs external scanning. If deployment is LAN-only with no public exposure, this shrinks significantly — deployment topology must be confirmed with the operator before finalizing this row. |
| **12** | Support information security with organizational policies and programs | No `SECURITY.md`, incident-response plan, or formal security policy document found in the repo. | No documented information-security policy, risk-assessment process, or incident-response plan (Req 12.1, 12.10). | **Yes** — required regardless of SAQ type; every PCI-scoped merchant needs a baseline security policy and an incident-response plan naming who to call if a breach is suspected. This is a low-cost, high-value first deliverable since it requires no code changes. |

---

## Secure Software Standard (SSS) scoping fork — business-model dependent

The analysis above assumes LotSpot is operated by a single merchant (the
store owner) as merchant-owned software, in which case only the merchant-side
PCI-DSS SAQ applies (per the scope decision above). **This changes if LotSpot
is instead sold or licensed as a product to other merchants** and, in that
distribution model, LotSpot's own software ever handles, transmits, or
influences the handling of PAN (e.g., a future integration where LotSpot's
app itself receives tokenized-but-payment-related data, controls terminal
invocation logic that is deemed "payment software," or is marketed as a
payment-enabling platform). In that case:

- The **PCI Software Security Framework (SSF)**, specifically the **Secure
  Software Standard (SSS)** and/or **Secure Software Lifecycle (SLC)**
  standard, applies to LotSpot as a *software vendor* obligation, separate
  from and in addition to each individual merchant-customer's own DSS SAQ.
  Source: PCI SSC, "Software Security Framework," pcisecuritystandards.org/
  assessors_and_solutions/software_security_framework (retrieved 2026-09-05).
- SSS/SLC assessment is done by a PCI SSC-recognized Secure Software Assessor
  against LotSpot's SDLC (secure coding practices, threat modeling, security
  testing, vulnerability response process for the *product*), not against a
  single deployed instance.
- This is a **business-model decision, not a code decision** — it should be
  flagged to the product/business owner now, before any multi-tenant or
  resold distribution model is finalized, because SSS assessment cost and
  lead time are substantial and are orthogonal to the SAQ path chosen above.

**Recommendation:** if/when LotSpot moves toward being sold to other
merchants rather than operated single-tenant, re-open this gap register with
an explicit SSS/SLC scoping conversation before any payment-terminal
integration ships in that distribution model.

---

## Summary of top 3 gaps to close first (regardless of final SAQ)

1. **Req 8 — Authentication.** Eliminate the shared default admin account,
   add per-user accounts/roles, MFA, login lockout, and an idle session
   timeout. Evidence: `backend/auth.py:17-29, 45-56, 72-97`.
2. **Req 10 — Audit logging.** Build a general `audit_log` table covering all
   privileged actions (not just sales/voids). Evidence: `backend/db.py:37-64`
   shows sales-only audit columns; no general audit table exists.
3. **Req 12 — Security policy & incident response.** Write a baseline
   `SECURITY.md`/incident-response doc — zero code cost, immediately closes a
   universally-required row.

These three are recommended independent of which SAQ (P2PE vs A-EP) is
finalized once the terminal vendor is selected, and independent of the
merchant-vs-software-vendor business-model fork above.
