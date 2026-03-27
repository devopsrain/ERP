"""
Parquet → PostgreSQL one-time migration script (Gap 7).

Reads legacy flat Parquet files from the data/ directory and upserts
their rows into the corresponding PostgreSQL tables, adding company_id='default'
to any row that is missing it.

Usage (run once, from the web/ directory):
    python services/parquet_migration.py

Or with a specific company_id for the legacy data:
    LEGACY_COMPANY_ID=my-company-uuid python services/parquet_migration.py

Safe to re-run — every INSERT uses ON CONFLICT DO NOTHING so no row is
duplicated if the script is interrupted and restarted.

After a successful run you may archive / delete the .parquet files:
    mkdir -p data/migrated && mv data/*.parquet data/migrated/
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# ── allow running from the web/ directory ─────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_conn

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

LEGACY_COMPANY_ID = os.environ.get("LEGACY_COMPANY_ID", "default")
DATA_DIR = Path(__file__).parent.parent / "data"


def _load_parquet(filename: str):
    """Return a list-of-dicts from a Parquet file, or [] if missing/unreadable."""
    path = DATA_DIR / filename
    if not path.exists():
        logger.info("  skip (not found): %s", path)
        return []
    try:
        import pandas as pd
        df = pd.read_parquet(path)
        if "company_id" not in df.columns:
            df["company_id"] = LEGACY_COMPANY_ID
        else:
            df["company_id"] = df["company_id"].fillna(LEGACY_COMPANY_ID)
        return df.to_dict("records")
    except Exception as e:
        logger.error("  failed to read %s: %s", filename, e)
        return []


# ── per-table migration functions ─────────────────────────────────

def migrate_transactions():
    rows = _load_parquet("transactions.parquet")
    if not rows:
        return
    logger.info("transactions: migrating %d rows …", len(rows))
    with get_conn() as conn:
        with conn.cursor() as cur:
            ok = 0
            for r in rows:
                try:
                    cur.execute(
                        """INSERT INTO transactions
                           (id, company_id, date, description, amount,
                            transaction_type, reference, is_flagged, created_at)
                           VALUES (%(id)s, %(company_id)s, %(date)s, %(description)s,
                                   %(amount)s, %(transaction_type)s, %(reference)s,
                                   %(is_flagged)s, %(created_at)s)
                           ON CONFLICT (id) DO NOTHING""",
                        {
                            "id":               r.get("id") or r.get("transaction_id"),
                            "company_id":       r.get("company_id", LEGACY_COMPANY_ID),
                            "date":             r.get("date"),
                            "description":      r.get("description", ""),
                            "amount":           float(r.get("amount", 0)),
                            "transaction_type": r.get("transaction_type", r.get("type", "")),
                            "reference":        r.get("reference", ""),
                            "is_flagged":       bool(r.get("is_flagged", False)),
                            "created_at":       r.get("created_at"),
                        },
                    )
                    ok += 1
                except Exception as e:
                    logger.warning("  row skip: %s", e)
    logger.info("  transactions: %d upserted", ok)


def migrate_inventory_items():
    rows = _load_parquet("inventory_items.parquet")
    if not rows:
        return
    logger.info("inventory_items: migrating %d rows …", len(rows))
    with get_conn() as conn:
        with conn.cursor() as cur:
            ok = 0
            for r in rows:
                try:
                    cur.execute(
                        """INSERT INTO inventory_items
                           (item_id, company_id, name, sku, category, description,
                            unit_of_measure, unit_cost, quantity_on_hand,
                            reorder_point, reorder_quantity, location, status, created_at)
                           VALUES (%(item_id)s, %(company_id)s, %(name)s, %(sku)s,
                                   %(category)s, %(description)s, %(unit_of_measure)s,
                                   %(unit_cost)s, %(quantity_on_hand)s, %(reorder_point)s,
                                   %(reorder_quantity)s, %(location)s, %(status)s, %(created_at)s)
                           ON CONFLICT (item_id, company_id) DO NOTHING""",
                        {
                            "item_id":          r.get("item_id"),
                            "company_id":       r.get("company_id", LEGACY_COMPANY_ID),
                            "name":             r.get("name", ""),
                            "sku":              r.get("sku", ""),
                            "category":         r.get("category", ""),
                            "description":      r.get("description", ""),
                            "unit_of_measure":  r.get("unit_of_measure", "pcs"),
                            "unit_cost":        float(r.get("unit_cost", 0)),
                            "quantity_on_hand": float(r.get("quantity_on_hand", 0)),
                            "reorder_point":    float(r.get("reorder_point", 0)),
                            "reorder_quantity": float(r.get("reorder_quantity", 0)),
                            "location":         r.get("location", ""),
                            "status":           r.get("status", "active"),
                            "created_at":       r.get("created_at"),
                        },
                    )
                    ok += 1
                except Exception as e:
                    logger.warning("  row skip: %s", e)
    logger.info("  inventory_items: %d upserted", ok)


def migrate_cpo_records():
    rows = _load_parquet("cpo_records.parquet")
    if not rows:
        return
    logger.info("cpo_records: migrating %d rows …", len(rows))
    with get_conn() as conn:
        with conn.cursor() as cur:
            ok = 0
            for r in rows:
                try:
                    cur.execute(
                        """INSERT INTO cpo_records
                           (id, company_id, name, date, amount, bid_name,
                            is_returned, returned_date, created_at)
                           VALUES (%(id)s, %(company_id)s, %(name)s, %(date)s,
                                   %(amount)s, %(bid_name)s, %(is_returned)s,
                                   %(returned_date)s, %(created_at)s)
                           ON CONFLICT (id) DO NOTHING""",
                        {
                            "id":            r.get("id"),
                            "company_id":    r.get("company_id", LEGACY_COMPANY_ID),
                            "name":          r.get("name", ""),
                            "date":          r.get("date", ""),
                            "amount":        float(r.get("amount", 0)),
                            "bid_name":      r.get("bid_name", ""),
                            "is_returned":   r.get("is_returned", "No"),
                            "returned_date": r.get("returned_date", ""),
                            "created_at":    r.get("created_at"),
                        },
                    )
                    ok += 1
                except Exception as e:
                    logger.warning("  row skip: %s", e)
    logger.info("  cpo_records: %d upserted", ok)


def migrate_bid_records():
    rows = _load_parquet("bid_records.parquet")
    if not rows:
        return
    logger.info("bid_records: migrating %d rows …", len(rows))
    with get_conn() as conn:
        with conn.cursor() as cur:
            ok = 0
            for r in rows:
                try:
                    cur.execute(
                        """INSERT INTO bid_records
                           (id, company_id, title, reference_number, organization,
                            description, category, status, deadline, bid_amount,
                            currency, notes, created_at)
                           VALUES (%(id)s, %(company_id)s, %(title)s, %(reference_number)s,
                                   %(organization)s, %(description)s, %(category)s,
                                   %(status)s, %(deadline)s, %(bid_amount)s,
                                   %(currency)s, %(notes)s, %(created_at)s)
                           ON CONFLICT (id) DO NOTHING""",
                        {
                            "id":               r.get("id"),
                            "company_id":       r.get("company_id", LEGACY_COMPANY_ID),
                            "title":            r.get("title", ""),
                            "reference_number": r.get("reference_number", ""),
                            "organization":     r.get("organization", ""),
                            "description":      r.get("description", ""),
                            "category":         r.get("category", ""),
                            "status":           r.get("status", "open"),
                            "deadline":         r.get("deadline", ""),
                            "bid_amount":       float(r.get("bid_amount", 0)),
                            "currency":         r.get("currency", "ETB"),
                            "notes":            r.get("notes", ""),
                            "created_at":       r.get("created_at"),
                        },
                    )
                    ok += 1
                except Exception as e:
                    logger.warning("  row skip: %s", e)
    logger.info("  bid_records: %d upserted", ok)


def migrate_vat(table: str, file: str):
    rows = _load_parquet(file)
    if not rows:
        return
    logger.info("%s: migrating %d rows …", table, len(rows))
    with get_conn() as conn:
        with conn.cursor() as cur:
            ok = 0
            id_col = {"income_records": "income_id",
                      "expense_records": "expense_id"}.get(file.replace(".parquet", ""), "id")
            for r in rows:
                rid = r.get(id_col) or r.get("id")
                if not rid:
                    continue
                try:
                    cur.execute(
                        f"""INSERT INTO {table}
                            SELECT %s, %s, NOW(), %s, %s, %s, %s, 15.0, %s,
                                   %s, %s, %s, %s, NOW(), '', TRUE
                            WHERE NOT EXISTS (
                                SELECT 1 FROM {table}
                                WHERE {id_col} = %s
                            )""",
                        (rid,
                         r.get("company_id", LEGACY_COMPANY_ID),
                         r.get("description", ""),
                         r.get("category", ""),
                         float(r.get("gross_amount", r.get("amount", 0))),
                         r.get("vat_type", "standard"),
                         float(r.get("vat_amount", 0)),
                         float(r.get("net_amount", 0)),
                         r.get("customer_name", r.get("supplier_name", "")),
                         r.get("customer_tin", r.get("supplier_tin", "")),
                         r.get("invoice_number", r.get("receipt_number", "")),
                         rid),
                    )
                    ok += 1
                except Exception as e:
                    logger.warning("  row skip: %s", e)
    logger.info("  %s: %d upserted", table, ok)


# ── entry point ────────────────────────────────────────────────────

def run_all():
    logger.info("Starting Parquet → PostgreSQL migration (legacy company_id=%s)", LEGACY_COMPANY_ID)
    migrate_transactions()
    migrate_inventory_items()
    migrate_cpo_records()
    migrate_bid_records()
    migrate_vat("vat_income", "income_records.parquet")
    migrate_vat("vat_expenses", "expense_records.parquet")
    logger.info("Migration complete.  Review logs above for any skipped rows.")
    logger.info(
        "Once verified, archive the source files:\n"
        "  mkdir -p data/migrated && mv data/*.parquet data/migrated/"
    )


if __name__ == "__main__":
    run_all()
