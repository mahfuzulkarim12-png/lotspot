"""Per-jurisdiction sales tax computation: integer cents and basis points
only, no floats anywhere. Kept separate from app.py's route wiring so the
tax math is independently readable and testable.
"""

import sqlite3

import db

BPS_DENOMINATOR = 10000  # basis points; 10000 bps == 100%


def round_half_up_bps(amount_cents: int, rate_bps: int) -> int:
    """Integer round-half-up of amount_cents * rate_bps / BPS_DENOMINATOR."""
    return (amount_cents * rate_bps + BPS_DENOMINATOR // 2) // BPS_DENOMINATOR


def tax_category_exists(conn: sqlite3.Connection, tax_category_id: int) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM tax_categories WHERE id = ?", (tax_category_id,)
        ).fetchone()
        is not None
    )


def default_tax_category_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT id FROM tax_categories WHERE name = ?", (db.GENERAL_MERCHANDISE_CATEGORY,)
    ).fetchone()
    return row["id"] if row else None


def compute_line_tax(
    conn: sqlite3.Connection,
    tax_category_id: int | None,
    line_total_cents: int,
    sold_at: str,
) -> tuple[int, str | None, list[dict]]:
    """Resolve the active tax_accounts for a product's tax_category as of the
    sale date and compute per-account tax on this line, integer cents only.
    Returns (total_tax_cents, tax_category_name, [{tax_account_id, tax_account_name,
    rate_bps, tax_cents}, ...])."""
    if tax_category_id is None:
        return 0, None, []

    category_row = conn.execute(
        "SELECT name FROM tax_categories WHERE id = ?", (tax_category_id,)
    ).fetchone()
    tax_category_name = category_row["name"] if category_row else None

    as_of_day = sold_at[:10]
    accounts = conn.execute(
        """SELECT tax_accounts.*
           FROM tax_accounts
           JOIN tax_category_accounts
             ON tax_category_accounts.tax_account_id = tax_accounts.id
           WHERE tax_category_accounts.tax_category_id = ?
             AND tax_accounts.effective_from <= ?
             AND (tax_accounts.effective_to IS NULL OR tax_accounts.effective_to >= ?)
           ORDER BY tax_accounts.name""",
        (tax_category_id, as_of_day, as_of_day),
    ).fetchall()

    tax_lines = []
    total_tax_cents = 0
    for row in accounts:
        account = db.row_to_dict(row)
        tax_cents = round_half_up_bps(line_total_cents, account["rate_bps"])
        total_tax_cents += tax_cents
        tax_lines.append(
            {
                "tax_account_id": account["id"],
                "tax_account_name": account["name"],
                "rate_bps": account["rate_bps"],
                "tax_cents": tax_cents,
            }
        )
    return total_tax_cents, tax_category_name, tax_lines


def aggregate_tax_breakdown(sales: list[dict]) -> list[dict]:
    """Sum each sale's per-jurisdiction sale_tax_lines across a whole
    transaction, so a multi-item checkout shows one row per tax_account."""
    breakdown: dict[int, dict] = {}
    for sale in sales:
        for line in sale.get("tax_lines", []):
            key = line["tax_account_id"]
            entry = breakdown.get(key)
            if entry is None:
                entry = {
                    "tax_account_id": line["tax_account_id"],
                    "tax_account_name": line["tax_account_name"],
                    "rate_bps": line["rate_bps"],
                    "tax_cents": 0,
                }
                breakdown[key] = entry
            entry["tax_cents"] += line["tax_cents"]
    return sorted(breakdown.values(), key=lambda entry: entry["tax_account_name"])
