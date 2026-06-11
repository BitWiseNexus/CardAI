-- CardAI — Supabase PostgreSQL schema
-- Run this once in the Supabase SQL Editor to initialise your project.

-- ============================================================
-- Core credit cards table
-- ============================================================
CREATE TABLE IF NOT EXISTS credit_cards (
    id                      SERIAL PRIMARY KEY,
    name                    TEXT        NOT NULL,
    issuer                  TEXT        NOT NULL,
    annual_fee              NUMERIC(10, 2) NOT NULL DEFAULT 0,
    regular_apr_low         NUMERIC(5, 2)  NOT NULL DEFAULT 0,
    regular_apr_high        NUMERIC(5, 2)  NOT NULL DEFAULT 0,
    signup_bonus            TEXT,
    signup_bonus_value_usd  NUMERIC(10, 2)          DEFAULT 0,

    -- Reward multipliers (points/miles/cash-back per $1 spent)
    travel_multiplier       NUMERIC(4, 2) DEFAULT 1.0,
    dining_multiplier       NUMERIC(4, 2) DEFAULT 1.0,
    groceries_multiplier    NUMERIC(4, 2) DEFAULT 1.0,
    gas_multiplier          NUMERIC(4, 2) DEFAULT 1.0,
    online_shopping_mult    NUMERIC(4, 2) DEFAULT 1.0,
    other_multiplier        NUMERIC(4, 2) DEFAULT 1.0,

    lounge_access           BOOLEAN       DEFAULT FALSE,
    foreign_transaction_fee NUMERIC(4, 2) DEFAULT 0,
    credit_score_required   TEXT          DEFAULT 'Good'
                                CHECK (credit_score_required IN ('Excellent','Good','Fair','Poor')),
    source_url              TEXT,
    description             TEXT,   -- qualitative text used for FAISS embedding
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (name, issuer)
);

-- ============================================================
-- Auto-update updated_at on every row change
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_cards_updated_at ON credit_cards;
CREATE TRIGGER trg_cards_updated_at
    BEFORE UPDATE ON credit_cards
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- Indexes for common query patterns
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_cards_annual_fee   ON credit_cards (annual_fee);
CREATE INDEX IF NOT EXISTS idx_cards_apr_low      ON credit_cards (regular_apr_low);
CREATE INDEX IF NOT EXISTS idx_cards_lounge       ON credit_cards (lounge_access);
CREATE INDEX IF NOT EXISTS idx_cards_issuer       ON credit_cards (issuer);
CREATE INDEX IF NOT EXISTS idx_cards_credit_score ON credit_cards (credit_score_required);

-- ============================================================
-- Phase 3: multi-region + search-agent cache columns
-- (idempotent — safe to run on an existing Phase 1 table)
-- ============================================================
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

-- India-specific reward multipliers
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS fuel_multiplier NUMERIC(4,2) DEFAULT 1.0;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS utilities_multiplier NUMERIC(4,2) DEFAULT 1.0;
ALTER TABLE credit_cards ADD COLUMN IF NOT EXISTS emi_multiplier NUMERIC(4,2) DEFAULT 1.0;

CREATE INDEX IF NOT EXISTS idx_cards_region     ON credit_cards (region);
CREATE INDEX IF NOT EXISTS idx_cards_fetched_at ON credit_cards (data_fetched_at);

-- ============================================================
-- Row-Level Security (enable for production)
-- ============================================================
-- ALTER TABLE credit_cards ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY "public read" ON credit_cards FOR SELECT USING (true);
-- CREATE POLICY "service write" ON credit_cards FOR ALL
--   USING (auth.role() = 'service_role');
