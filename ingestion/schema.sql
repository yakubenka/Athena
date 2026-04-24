-- Athena DB schema v1.3
-- Source of truth: research/roadmap-v1.3.md (Phase 2, Week 2)
-- Idempotent: safe to re-run.

-- =========================================================================
-- Wallets
-- =========================================================================
CREATE TABLE IF NOT EXISTS wallets (
    address VARCHAR(42) PRIMARY KEY,
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    total_volume NUMERIC(20, 6) DEFAULT 0,
    total_trades INT DEFAULT 0,
    total_maker_volume NUMERIC(20, 6) DEFAULT 0,
    total_taker_volume NUMERIC(20, 6) DEFAULT 0,
    total_maker_trades INT DEFAULT 0,
    total_taker_trades INT DEFAULT 0,
    proxy_wallet VARCHAR(42),
    label VARCHAR(50),
    tier CHAR(1),  -- 'S' True Smart Maker, 'C' Category Specialist, 'A', 'B', 'X' blacklist
    -- Sirolly wash detection
    wash_score NUMERIC(5, 4),
    wash_cluster_id VARCHAR(50),
    is_suspected_wash BOOLEAN DEFAULT FALSE,
    -- Reichenbach consistency
    consecutive_profitable_months INT DEFAULT 0,
    max_consecutive_5k_months INT DEFAULT 0,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_wallets_wash ON wallets(is_suspected_wash);
CREATE INDEX IF NOT EXISTS idx_wallets_consistency ON wallets(max_consecutive_5k_months DESC);

-- =========================================================================
-- Markets
-- =========================================================================
CREATE TABLE IF NOT EXISTS markets (
    condition_id VARCHAR(66) PRIMARY KEY,
    question TEXT NOT NULL,
    category VARCHAR(50),
    category_tier VARCHAR(10),  -- HIGH / MED / LOW maker-taker gap
    est_wash_fraction NUMERIC(5, 4),  -- from category_stats (Sirolly)
    created_at TIMESTAMPTZ,
    end_date TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    resolved_outcome VARCHAR(10),
    total_volume NUMERIC(20, 6)
);

-- Polymarket CLOB token IDs for mapping on-chain asset IDs -> (market, outcome).
-- These are uint256 values from the CTF framework; store as TEXT so we don't
-- lose precision. Added after the initial backfill, so IF NOT EXISTS.
ALTER TABLE markets ADD COLUMN IF NOT EXISTS yes_token_id TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS no_token_id TEXT;
CREATE INDEX IF NOT EXISTS idx_markets_yes_token ON markets(yes_token_id);
CREATE INDEX IF NOT EXISTS idx_markets_no_token ON markets(no_token_id);

-- =========================================================================
-- Trades
-- =========================================================================
CREATE TABLE IF NOT EXISTS trades (
    id BIGSERIAL PRIMARY KEY,
    tx_hash VARCHAR(66) NOT NULL,
    log_index INT NOT NULL,
    maker_address VARCHAR(42) NOT NULL,
    taker_address VARCHAR(42) NOT NULL,
    condition_id VARCHAR(66) NOT NULL REFERENCES markets(condition_id),
    outcome VARCHAR(10) NOT NULL,
    taker_side VARCHAR(4) NOT NULL,
    size NUMERIC(20, 6) NOT NULL,
    price NUMERIC(10, 6) NOT NULL,
    usdc_amount NUMERIC(20, 6) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    UNIQUE(tx_hash, log_index)
);
CREATE INDEX IF NOT EXISTS idx_trades_maker ON trades(maker_address);
CREATE INDEX IF NOT EXISTS idx_trades_taker ON trades(taker_address);
CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(condition_id);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp DESC);

-- Block number retained for resumable backfill (MAX(block_number) is the cursor).
ALTER TABLE trades ADD COLUMN IF NOT EXISTS block_number BIGINT;
CREATE INDEX IF NOT EXISTS idx_trades_block ON trades(block_number);

-- =========================================================================
-- Positions
-- =========================================================================
CREATE TABLE IF NOT EXISTS wallet_positions (
    wallet VARCHAR(42) NOT NULL REFERENCES wallets(address),
    condition_id VARCHAR(66) NOT NULL REFERENCES markets(condition_id),
    outcome VARCHAR(10) NOT NULL,
    net_shares NUMERIC(20, 6),
    avg_cost NUMERIC(10, 6),
    realised_pnl NUMERIC(20, 6),
    PRIMARY KEY (wallet, condition_id, outcome)
);

-- =========================================================================
-- Wallet metrics (Akey + Becker + Reichenbach + Sirolly features)
-- =========================================================================
CREATE TABLE IF NOT EXISTS wallet_metrics (
    address VARCHAR(42) PRIMARY KEY REFERENCES wallets(address),

    -- PnL
    realised_pnl NUMERIC(20, 6),
    unrealised_pnl NUMERIC(20, 6),
    total_pnl NUMERIC(20, 6),

    -- Akey features
    frac_maker_volume NUMERIC(5, 4),
    frac_maker_trades NUMERIC(5, 4),
    frac_extreme_price NUMERIC(5, 4),

    -- Becker features
    frac_yes_trades NUMERIC(5, 4),
    frac_no_trades NUMERIC(5, 4),
    frac_yes_at_longshot NUMERIC(5, 4),
    frac_no_at_longshot NUMERIC(5, 4),

    -- Performance
    win_rate NUMERIC(5, 4),
    num_resolved_trades INT,
    excess_hit_rate NUMERIC(10, 6),
    sharpe_like NUMERIC(10, 4),
    alpha_per_trade NUMERIC(10, 6),

    -- Activity / concentration
    avg_trade_size NUMERIC(20, 6),
    median_holding_hours NUMERIC(10, 2),
    category_hhi NUMERIC(5, 4),
    counterparty_hhi NUMERIC(5, 4),
    dominant_category VARCHAR(50),

    -- Temporal / consistency (Reichenbach)
    active_since TIMESTAMPTZ,
    active_in_current_regime BOOLEAN,
    profitable_months_total INT,
    max_consecutive_profitable_months INT,
    max_consecutive_5k_plus_months INT,

    -- Wash detection (Sirolly)
    wash_score NUMERIC(5, 4),
    pnl_to_volume_ratio NUMERIC(10, 6),
    rapid_open_close_ratio NUMERIC(5, 4),

    last_trade_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_metrics_pnl ON wallet_metrics(total_pnl DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_maker ON wallet_metrics(frac_maker_volume DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_consistency
    ON wallet_metrics(max_consecutive_5k_plus_months DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_wash ON wallet_metrics(wash_score);

-- =========================================================================
-- Monthly PnL history (Reichenbach consistency tracking)
-- =========================================================================
CREATE TABLE IF NOT EXISTS wallet_monthly_pnl (
    wallet VARCHAR(42) NOT NULL REFERENCES wallets(address),
    month DATE NOT NULL,
    monthly_pnl NUMERIC(20, 6),
    monthly_volume NUMERIC(20, 6),
    num_trades INT,
    num_resolved INT,
    win_rate NUMERIC(5, 4),
    PRIMARY KEY (wallet, month)
);
CREATE INDEX IF NOT EXISTS idx_wmp_month ON wallet_monthly_pnl(month DESC);

-- =========================================================================
-- Per-category wallet metrics (Becker)
-- =========================================================================
CREATE TABLE IF NOT EXISTS wallet_category_metrics (
    wallet VARCHAR(42) NOT NULL REFERENCES wallets(address),
    category VARCHAR(50) NOT NULL,
    num_trades INT,
    volume NUMERIC(20, 6),
    frac_maker_volume NUMERIC(5, 4),
    pnl NUMERIC(20, 6),
    win_rate NUMERIC(5, 4),
    excess_return NUMERIC(10, 6),
    avg_edge NUMERIC(10, 6),
    PRIMARY KEY (wallet, category)
);

-- =========================================================================
-- Category aggregates
-- =========================================================================
CREATE TABLE IF NOT EXISTS category_stats (
    category VARCHAR(50) PRIMARY KEY,
    num_markets INT,
    total_volume NUMERIC(20, 6),
    avg_taker_return NUMERIC(10, 6),
    avg_maker_return NUMERIC(10, 6),
    maker_taker_gap NUMERIC(10, 6),
    tier VARCHAR(10),  -- HIGH / MED / LOW
    est_wash_fraction NUMERIC(5, 4),
    updated_at TIMESTAMPTZ
);

-- =========================================================================
-- Wash cluster registry (Sirolly)
-- =========================================================================
CREATE TABLE IF NOT EXISTS wash_clusters (
    cluster_id VARCHAR(50) PRIMARY KEY,
    num_wallets INT,
    total_volume NUMERIC(20, 6),
    aggregate_pnl NUMERIC(20, 6),
    first_detected TIMESTAMPTZ,
    last_updated TIMESTAMPTZ,
    confidence_score NUMERIC(5, 4),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS wash_cluster_membership (
    cluster_id VARCHAR(50) REFERENCES wash_clusters(cluster_id),
    wallet VARCHAR(42) REFERENCES wallets(address),
    added_at TIMESTAMPTZ DEFAULT NOW(),
    volume_in_cluster NUMERIC(20, 6),
    PRIMARY KEY (cluster_id, wallet)
);

-- =========================================================================
-- Watchlist
-- =========================================================================
CREATE TABLE IF NOT EXISTS watchlist (
    address VARCHAR(42) PRIMARY KEY REFERENCES wallets(address),
    added_at TIMESTAMPTZ DEFAULT NOW(),
    tier CHAR(1),
    specialty_category VARCHAR(50),
    copy_enabled BOOLEAN DEFAULT FALSE,
    copy_size_usdc NUMERIC(10, 2),
    notes TEXT,
    last_reviewed TIMESTAMPTZ
);

-- =========================================================================
-- Signals log
-- =========================================================================
CREATE TABLE IF NOT EXISTS signals (
    id BIGSERIAL PRIMARY KEY,
    wallet VARCHAR(42),
    wallet_tier CHAR(1),
    condition_id VARCHAR(66),
    category VARCHAR(50),
    category_tier VARCHAR(10),
    outcome VARCHAR(10),
    taker_side VARCHAR(4),
    role VARCHAR(5),
    price NUMERIC(10, 6),
    size NUMERIC(20, 6),
    is_optimism_extraction BOOLEAN,
    is_counter_flow BOOLEAN,
    signal_strength VARCHAR(20),
    detected_at TIMESTAMPTZ DEFAULT NOW(),
    trade_timestamp TIMESTAMPTZ,
    latency_seconds INT,
    action_taken VARCHAR(50),
    prometheus_trade_id BIGINT
);
