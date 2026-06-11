"""
Phase 3 migration runner — adds multi-region columns to credit_cards.

Connects directly to the Supabase Postgres instance (PostgREST cannot run DDL).
Requires SUPABASE_DB_PASSWORD in .env (Supabase Dashboard → Settings → Database).

Usage:
    python -m scripts.migrate_phase3

If the direct connection fails (some Supabase projects are IPv6-only),
copy the SQL printed below into the Supabase SQL Editor instead.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

MIGRATION_SQL = """
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS region TEXT DEFAULT 'US';
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS currency TEXT DEFAULT 'USD';
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS reward_type TEXT;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS reward_rate_description TEXT;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS fuel_surcharge_waiver BOOLEAN DEFAULT FALSE;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS domestic_lounge_access INTEGER;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS international_lounge_access INTEGER;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS joining_fee NUMERIC(10,2) DEFAULT 0;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS milestone_benefits TEXT;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS data_fetched_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS fuel_multiplier NUMERIC(4,2) DEFAULT 1.0;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS utilities_multiplier NUMERIC(4,2) DEFAULT 1.0;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS emi_multiplier NUMERIC(4,2) DEFAULT 1.0;
CREATE INDEX IF NOT EXISTS idx_cards_region     ON credit_cards (region);
CREATE INDEX IF NOT EXISTS idx_cards_fetched_at ON credit_cards (data_fetched_at);
"""


def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL", "")
    password = os.environ.get("SUPABASE_DB_PASSWORD", "")

    project_ref_match = re.match(r"https://([a-z0-9]+)\.supabase\.co", supabase_url)
    if not project_ref_match or not password:
        print("SUPABASE_URL or SUPABASE_DB_PASSWORD missing from .env — run this SQL manually")
        print("in the Supabase SQL Editor (https://supabase.com/dashboard → SQL Editor):")
        print(MIGRATION_SQL)
        sys.exit(1)

    project_ref = project_ref_match.group(1)

    import psycopg2

    # Try the session pooler (IPv4) first, then the direct host (often IPv6-only)
    candidates = [
        dict(host=f"aws-0-ap-south-1.pooler.supabase.com", port=5432,
             user=f"postgres.{project_ref}", password=password, dbname="postgres"),
        dict(host=f"aws-0-us-east-1.pooler.supabase.com", port=5432,
             user=f"postgres.{project_ref}", password=password, dbname="postgres"),
        dict(host=f"db.{project_ref}.supabase.co", port=5432,
             user="postgres", password=password, dbname="postgres"),
    ]

    last_error: Exception | None = None
    for params in candidates:
        try:
            print(f"Connecting to {params['host']}…")
            conn = psycopg2.connect(connect_timeout=10, **params)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(MIGRATION_SQL)
            conn.close()
            print("[OK] Phase 3 migration applied successfully.")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"   failed: {exc}")

    print("\n[FAILED] Could not connect directly. Run this SQL in the Supabase SQL Editor instead:")
    print(MIGRATION_SQL)
    sys.exit(1)


if __name__ == "__main__":
    main()
