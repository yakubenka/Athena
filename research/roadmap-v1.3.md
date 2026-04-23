# Polymarket Intelligence — Roadmap v1.3

**Автор:** Artsiom Yakubenka
**Старт:** Apr 17, 2026
**Версия:** 1.3 (обновлено 18 Apr 2026)
**Кодовое имя проекта:** Athena

**Цель:** Построить систему, которая находит smart money кошельки на Polymarket, классифицирует их по устойчивому edge, фильтрует wash trading, и автоматически копирует их сделки через Prometheus.

---

## Changelog v1.2 → v1.3

Обновление интегрирует две дополнительные академические работы:

**Sirolly, Ma, Kanoria, Sethi (Nov 2025)** — "Network-Based Detection of Wash Trading"

- **25% среднего volume Polymarket — wash trading**, пик 60% в декабре 2024
- **14% всех 1.26M кошельков** показывают wash patterns
- 45% sports volume, 17% elections, 12% politics — wash
- Network-based detection algorithm (PageRank-like, 3 итерации)
- Peaks коррелируют с token airdrop rumors, не с реальными событиями

**Reichenbach & Walther (Dec 2025)** — "Exploring Decentralized Prediction Markets: Accuracy, Skill, and Bias on Polymarket"

- 124.5M сделок, 974k трейдеров, $48B volume
- **70% юзеров теряют деньги** (подтверждает Akey)
- **Skill persistent** — есть стабильные winners
- Overtrading YES option (подтверждает Becker's optimism tax)
- **No general longshot bias на Polymarket** (отличается от Kalshi)
- Реальные цифры persistence: 72 кошелька зарабатывают >$5k в 9 подряд месяцах, 35 — в 12 подряд

**Ключевые изменения в Athena:**

1. **Wash trading detection** — полноценный модуль по методологии Sirolly
2. **Реалистичный target pool** — 15–35 "True Tier S", не сотни
3. **Monthly wash re-scoring** — wash waves коррелируют с token rumors
4. **Consistency-based Tier S criteria** — требование 9+ месяцев profitability
5. **Peer-validated narrative** — 4 независимых академических источника подтверждают гипотезу
6. **Polymarket-specific nuance** — нет общего longshot bias, но YES overtrading есть

---

## Key data points (все четыре статьи)

### Akey et al. (Polymarket, Mar 2026) — 1.4M users, $20B, 70M trades

- Top 0.1% = 58.5% прибылей, top 1% = 84.1%
- 70.8% юзеров теряют деньги
- Frac Maker Volume: −35.9 п.п. к loss probability
- Category HHI: +13.6 п.п., Counterparty HHI: −5.2 п.п.

### Becker (Kalshi, Jan 2026) — 72.1M trades, $18.26B

- Makers +1.12%, Takers −1.12% avg excess return
- **Optimism Tax:** YES 1¢ = −41% EV, NO 1¢ = +23% EV
- Category gaps: Finance 0.17 → World Events 7.32 п.п.
- До Q3 2024 asymmetry была перевёрнута

### Sirolly et al. (Polymarket, Nov 2025) — 1.26M wallets

- **25% avg volume = wash trading** (range 5%–60%)
- **14% wallets show wash patterns** (≈176k кошельков)
- Sports: 45% wash, Elections: 17%, Politics: 12%, Crypto: 3%
- Pattern: wash peaks → token airdrop rumors
- Network-homophily: wash traders clustered, makers heterophily

### Reichenbach & Walther (Polymarket, Dec 2025) — 124.5M trades, 974k traders, $48B

- 70% негативный PnL
- **Only 840 кошельков** заработали >$100k (0.033%)
- **Only 72 кошелька** делают $5k+ в 9+ consecutive months
- **Only 35 кошельков** делают $5k+ в 12+ consecutive months
- YES overtrading подтверждён
- No general longshot bias (Polymarket-specific)
- Cross-validation с Akey: 70% = 70.8% loss rate

---

## Философия движения (без изменений)

- Валидируй раньше, чем кодишь
- Paper trade перед live (минимум 2 недели)
- Реюзай Prometheus-инфру везде
- Один фокус за раз
- **NEW:** Качество > количество — 10–30 STRONG сигналов в день лучше, чем 500 шумных

---

## Phase 1: Validation (Week 1)

### Week 1 — Dune Research + Pre-validation

**Понедельник–Вторник:**

- [ ] Dune Analytics (free tier)
- [ ] Изучить схему `polymarket.trades`
- [ ] Прочитать все 4 статьи целиком если ещё нет:
  - Akey et al. (Mar 2026)
  - Becker (Jan 2026)
  - Sirolly et al. (Nov 2025)
  - Reichenbach & Walther (Dec 2025)

**Среда — Queries:**

**Query #1 — Top-200 wallets maker/taker split (post-Q4 2024):**

```sql
WITH trades_dual AS (
  SELECT maker AS wallet, 'MAKER' AS role, size, price, timestamp
  FROM polymarket.trades
  UNION ALL
  SELECT taker AS wallet, 'TAKER' AS role, size, price, timestamp
  FROM polymarket.trades
),
wallet_stats AS (
  SELECT
    wallet,
    SUM(CASE WHEN role = 'MAKER' THEN size * price ELSE 0 END) AS maker_volume,
    SUM(size * price) AS total_volume,
    COUNT(*) AS num_trades
  FROM trades_dual
  WHERE timestamp >= '2024-10-01'
  GROUP BY wallet
)
SELECT wallet, maker_volume / NULLIF(total_volume, 0) AS frac_maker_volume,
       total_volume, num_trades
FROM wallet_stats
WHERE num_trades > 50
ORDER BY total_volume DESC
LIMIT 200;
```

**Query #2 — Replicate Akey: profit concentration**
Проверить: top 1% забирают ~84%? Top 0.1% ~58%?

**Query #3 — Replicate Becker: category gaps on Polymarket**
Посчитать maker-taker gap отдельно для: Politics, Sports, Crypto, Finance, Entertainment, etc. Ожидаем Finance-подобные категории близко к нулю, Sports/Crypto средний, Media/World Events максимум.

**Query #4 — Replicate Reichenbach: consistency counts (CRITICAL)**

```sql
-- Кошельки, зарабатывающие $5k+ в N подряд месяцах
WITH monthly_pnl AS (
  SELECT
    wallet,
    DATE_TRUNC('month', timestamp) AS month,
    SUM(pnl_per_trade) AS monthly_pnl
  FROM trades_with_pnl
  GROUP BY wallet, month
),
consistency AS (
  SELECT
    wallet,
    COUNT(*) AS months_with_5k_plus
  FROM monthly_pnl
  WHERE monthly_pnl >= 5000
  GROUP BY wallet
)
SELECT * FROM consistency
WHERE months_with_5k_plus >= 9
ORDER BY months_with_5k_plus DESC;
```

**Ожидаем ~72 кошелька с 9+ месяцами, ~35 с 12+.** Это наш absolute best Tier S candidates.

**Query #5 — YES/NO asymmetry на Polymarket (Becker replication)**
Expected return для YES vs NO по ценовым бинам. Проверяем: подтверждается ли optimism tax?

**Query #6 — Basic wash trading screening (precursor to full Sirolly implementation)**

```sql
-- Кошельки с подозрительно низким PnL при высоком volume
SELECT
  wallet,
  total_volume,
  total_pnl,
  ABS(total_pnl) / NULLIF(total_volume, 0) AS pnl_to_volume_ratio,
  num_trades
FROM wallet_stats
WHERE total_volume > 100000
  AND ABS(total_pnl) / total_volume < 0.001  -- PnL/volume < 0.1%
ORDER BY total_volume DESC;
```

Кошельки с volume >$100k и PnL ≈ 0 — типичные wash cluster members. Их должно быть несколько тысяч.

**Query #7 — Counterparty clustering (simple wash indicator)**
Для каждого кошелька: какой % объёма с топ-5 контрпартнёрами? Если >80% — вероятный wash кластер.

**Query #8 — Temporal wash check**
Volume в период слухов о токене (октябрь 2025) vs спокойный период (май 2025). Если в октябре volume взорвался без реальных событий — это wash.

**Четверг:**

- [ ] Сравнить Query #4 с цифрами Reichenbach (72/35 кошельков) — если сходится, это твой primary target pool
- [ ] Manual inspection топ-20 кошельков из Query #4 на Polygonscan: возраст, деятельность, связанные кошельки
- [ ] Category tier assignment на основе Query #3
- [ ] Wash cluster detection на основе Query #6 + #7

**Пятница — Walk-forward test:**

- [ ] Train window: Q4 2024 – Oct 2025
- [ ] Test window: Nov 2025 – Apr 2026
- [ ] На train: identify consistency-based Tier S (Query #4 methodology)
- [ ] **Filter out wash suspects** (Query #6 + #7)
- [ ] Simulate copy-trading на test с 5–10 min latency
- [ ] Stratify results: with wash filter vs without — должна быть заметная разница
- [ ] Записать в `research-log.md`

### 🚦 Gate 1 (конец Week 1):

**Вопросы:**

1. Воспроизводится ли Akey (top 1% ≈ 80%+)?
2. Воспроизводится ли Reichenbach (~35–72 super-consistent wallets)?
3. Воспроизводится ли Becker's category variance?
4. Wash-filtered test PnL значимо выше unfiltered?
5. После всех filters остаётся достаточно сигналов для копирования?

- ✅ 4–5 → Phase 2
- ⚠️ 3 подтверждается слабее → это OK, Polymarket отличается от Kalshi
- ❌ 2 не подтверждается → проверь PnL calculation methodology
- ❌ 4 не подтверждается → возможно твой wash filter недостаточно строгий

---

## Phase 2: Foundation (Weeks 2–3)

### Week 2 — Infrastructure Setup

**Понедельник:**

- [ ] Репо `athena`
- [ ] Структура: `/ingestion`, `/metrics`, `/wash_detector` (NEW), `/monitoring`, `/alerts`, `/copy_trader`, `/dashboard`, `/tests`, `/research`
- [ ] Railway проект (отдельный от Prometheus)
- [ ] Postgres

**Вторник–Среда — Схема БД v1.3:**

```sql
-- Кошельки
CREATE TABLE wallets (
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
    tier CHAR(1),  -- 'S' (True Smart Maker), 'C' (Category Specialist), 'A', 'B', 'X' (blacklist)
    -- NEW: wash detection
    wash_score NUMERIC(5, 4),  -- 0.0 to 1.0, от Sirolly algorithm
    wash_cluster_id VARCHAR(50),  -- если в кластере, его ID
    is_suspected_wash BOOLEAN DEFAULT FALSE,
    -- NEW: Reichenbach consistency
    consecutive_profitable_months INT DEFAULT 0,
    max_consecutive_5k_months INT DEFAULT 0,
    notes TEXT
);

CREATE INDEX idx_wallets_wash ON wallets(is_suspected_wash);
CREATE INDEX idx_wallets_consistency ON wallets(max_consecutive_5k_months DESC);

-- Рынки
CREATE TABLE markets (
    condition_id VARCHAR(66) PRIMARY KEY,
    question TEXT NOT NULL,
    category VARCHAR(50),
    category_tier VARCHAR(10),  -- HIGH/MED/LOW gap
    -- NEW: wash contamination per category (Sirolly)
    est_wash_fraction NUMERIC(5, 4),  -- из category_stats
    created_at TIMESTAMPTZ,
    end_date TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    resolved_outcome VARCHAR(10),
    total_volume NUMERIC(20, 6)
);

-- Сделки (без изменений от v1.2)
CREATE TABLE trades (
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
CREATE INDEX idx_trades_maker ON trades(maker_address);
CREATE INDEX idx_trades_taker ON trades(taker_address);
CREATE INDEX idx_trades_market ON trades(condition_id);
CREATE INDEX idx_trades_timestamp ON trades(timestamp DESC);

-- Позиции
CREATE TABLE wallet_positions (
    wallet VARCHAR(42) NOT NULL REFERENCES wallets(address),
    condition_id VARCHAR(66) NOT NULL REFERENCES markets(condition_id),
    outcome VARCHAR(10) NOT NULL,
    net_shares NUMERIC(20, 6),
    avg_cost NUMERIC(10, 6),
    realised_pnl NUMERIC(20, 6),
    PRIMARY KEY (wallet, condition_id, outcome)
);

-- Общие метрики (дополнено wash-related полями)
CREATE TABLE wallet_metrics (
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
    max_consecutive_5k_plus_months INT,  -- ключевая Reichenbach метрика

    -- Wash detection (Sirolly)
    wash_score NUMERIC(5, 4),
    pnl_to_volume_ratio NUMERIC(10, 6),  -- indicator of wash
    rapid_open_close_ratio NUMERIC(5, 4),  -- turnover velocity

    last_trade_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_metrics_pnl ON wallet_metrics(total_pnl DESC);
CREATE INDEX idx_metrics_maker ON wallet_metrics(frac_maker_volume DESC);
CREATE INDEX idx_metrics_consistency ON wallet_metrics(max_consecutive_5k_plus_months DESC);
CREATE INDEX idx_metrics_wash ON wallet_metrics(wash_score);

-- Monthly PnL история (NEW — для consistency tracking)
CREATE TABLE wallet_monthly_pnl (
    wallet VARCHAR(42) NOT NULL REFERENCES wallets(address),
    month DATE NOT NULL,
    monthly_pnl NUMERIC(20, 6),
    monthly_volume NUMERIC(20, 6),
    num_trades INT,
    num_resolved INT,
    win_rate NUMERIC(5, 4),
    PRIMARY KEY (wallet, month)
);
CREATE INDEX idx_wmp_month ON wallet_monthly_pnl(month DESC);

-- Категорийные метрики (Becker)
CREATE TABLE wallet_category_metrics (
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

-- Категорийные агрегаты
CREATE TABLE category_stats (
    category VARCHAR(50) PRIMARY KEY,
    num_markets INT,
    total_volume NUMERIC(20, 6),
    avg_taker_return NUMERIC(10, 6),
    avg_maker_return NUMERIC(10, 6),
    maker_taker_gap NUMERIC(10, 6),
    tier VARCHAR(10),  -- HIGH/MED/LOW
    est_wash_fraction NUMERIC(5, 4),  -- NEW: от Sirolly (Sports 45%, etc)
    updated_at TIMESTAMPTZ
);

-- NEW: Wash cluster registry (Sirolly)
CREATE TABLE wash_clusters (
    cluster_id VARCHAR(50) PRIMARY KEY,
    num_wallets INT,
    total_volume NUMERIC(20, 6),
    aggregate_pnl NUMERIC(20, 6),
    first_detected TIMESTAMPTZ,
    last_updated TIMESTAMPTZ,
    confidence_score NUMERIC(5, 4),
    notes TEXT  -- "MAY cluster", "October 2025 airdrop farm", etc
);

CREATE TABLE wash_cluster_membership (
    cluster_id VARCHAR(50) REFERENCES wash_clusters(cluster_id),
    wallet VARCHAR(42) REFERENCES wallets(address),
    added_at TIMESTAMPTZ DEFAULT NOW(),
    volume_in_cluster NUMERIC(20, 6),
    PRIMARY KEY (cluster_id, wallet)
);

-- Watchlist (без изменений)
CREATE TABLE watchlist (
    address VARCHAR(42) PRIMARY KEY REFERENCES wallets(address),
    added_at TIMESTAMPTZ DEFAULT NOW(),
    tier CHAR(1),
    specialty_category VARCHAR(50),
    copy_enabled BOOLEAN DEFAULT FALSE,
    copy_size_usdc NUMERIC(10, 2),
    notes TEXT,
    last_reviewed TIMESTAMPTZ
);

-- Signals log (без изменений от v1.2)
CREATE TABLE signals (
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
```

**Четверг–Пятница:**

- [ ] Ingestion markets через Gamma API
- [ ] Category classifier
- [ ] Category tier assignment по результатам Week 1

**Deliverable Week 2:** Schema v1.3 + markets.

### Week 3 — Trades Backfill

**Понедельник–Вторник:**

- [ ] Ingestion trades (maker + taker addresses)
- [ ] Upsert wallets

**Среда–Четверг:**

- [ ] Запустить backfill (12–36 часов)

**Пятница:**

- [ ] Sanity check: сверить с числами всех 4 статей
  - Akey: ~70M trades, ~1.4M users (Q4 2022 – Oct 2025)
  - Reichenbach: ~124.5M trades, ~974k users (Nov 2022 – Sep 2025)
  - Твои цифры должны быть близки к Reichenbach, т.к. более свежие данные
- [ ] Git tag `v0.1-backfill-complete`

### 🚦 Gate 2: данные сошлись?

- ✅ → Phase 3
- ❌ → отладка

---

## Phase 3: Metrics, Wash Detection & Classification (Weeks 4–5)

### Week 4 — Metrics Engine + Wash Detector (NEW)

**Понедельник:**

- [ ] PnL engine v1
- [ ] Frac Maker Volume / Trades
- [ ] Frac Extreme Price

**Вторник — Becker features:**

- [ ] frac_yes_trades, frac_no_trades
- [ ] frac_yes_at_longshot, frac_no_at_longshot
- [ ] Per-trade excess return
- [ ] is_optimism_extraction flags

**Среда:**

- [ ] Win rate, excess hit rate, Sharpe-like
- [ ] Category HHI, Counterparty HHI
- [ ] Category stratified metrics → `wallet_category_metrics`
- [ ] Category aggregates → `category_stats`

**Четверг — Reichenbach consistency metrics (NEW):**

- [ ] Заполнить `wallet_monthly_pnl` по каждому кошельку
- [ ] Вычислить `max_consecutive_profitable_months` — максимум подряд положительных месяцев
- [ ] Вычислить `max_consecutive_5k_plus_months` — максимум подряд месяцев с PnL ≥ $5k
- [ ] **Identify the "Reichenbach 72"** — кошельки с ≥9 подряд $5k+ месяцами
- [ ] **Identify the "Reichenbach 35"** — кошельки с ≥12 подряд $5k+ месяцами

**Пятница — Sirolly Wash Detector v1 (NEW, КРИТИЧЕСКИЙ МОДУЛЬ):**

Реализация network-based algorithm:

```python
# /wash_detector/sirolly.py

import networkx as nx
import numpy as np
from collections import defaultdict

def initialize_scores(trades_df):
    """
    Stage 1: Score based on propensity to open/close positions rapidly.
    Higher score = more suspicious turnover pattern.
    """
    scores = {}
    for wallet in wallets:
        # Количество быстрых open/close циклов
        rapid_cycles = count_rapid_open_close(wallet, trades_df, max_hold_hours=24)
        total_positions = count_all_positions(wallet, trades_df)

        # PnL/Volume ratio (wash = близко к 0)
        pnl_vol_ratio = abs(wallet_pnl) / max(wallet_volume, 1)

        # Initial score: high turnover + low PnL/Volume = suspicious
        turnover_score = rapid_cycles / max(total_positions, 1)
        pnl_suspicion = 1.0 - min(pnl_vol_ratio * 100, 1.0)  # close to 0 PnL = high score

        scores[wallet] = 0.5 * turnover_score + 0.5 * pnl_suspicion

    return scores


def iterate_scores(scores, trades_df, n_iterations=3):
    """
    Stage 2: Iterative redistribution.
    Each wallet's score updated based on volume-weighted scores of counterparties.
    """
    # Построить граф: nodes = wallets, edges = trades, weighted by volume
    G = build_trade_graph(trades_df)

    for iteration in range(n_iterations):
        new_scores = {}
        for wallet in scores:
            counterparties = G.neighbors(wallet)
            if not counterparties:
                new_scores[wallet] = scores[wallet]
                continue

            total_volume = sum(G[wallet][cp]['volume'] for cp in counterparties)

            # Volume-weighted average of counterparty scores
            weighted_sum = sum(
                scores[cp] * G[wallet][cp]['volume']
                for cp in counterparties
            )

            new_scores[wallet] = weighted_sum / max(total_volume, 1)

        scores = new_scores

    return scores


def detect_wash_clusters(final_scores, trades_df, threshold=0.7):
    """
    Identify wash clusters among flagged wallets.
    Wallets with high scores that trade mostly with each other form clusters.
    """
    suspected = {w for w, s in final_scores.items() if s >= threshold}

    # Построить подграф только из подозрительных
    subgraph = build_trade_graph(trades_df, restrict_to=suspected)

    # Найти connected components (clusters)
    clusters = list(nx.connected_components(subgraph))

    # Отфильтровать тривиальные (size < 3)
    meaningful_clusters = [c for c in clusters if len(c) >= 3]

    return meaningful_clusters


def run_wash_detection(trades_df, threshold=0.7):
    """Main entry point."""
    print("Stage 1: Initialization...")
    initial_scores = initialize_scores(trades_df)

    print("Stage 2: Iterative redistribution (3 iterations)...")
    final_scores = iterate_scores(initial_scores, trades_df, n_iterations=3)

    print("Stage 3: Cluster detection...")
    clusters = detect_wash_clusters(final_scores, trades_df, threshold)

    return final_scores, clusters
```

- [ ] Реализовать по пунктам
- [ ] Запустить на backfill данных
- [ ] Expected result: ~14% кошельков помечены (по Sirolly), ~25% volume в кластерах
- [ ] Сохранить scores в `wallet_metrics.wash_score`, `is_suspected_wash`, `wash_cluster_id`
- [ ] Документировать найденные крупные кластеры в `wash_clusters`
- [ ] Incremental updater через cron (еженедельно)
- [ ] Git tag `v0.2-metrics-live`

### Week 5 — Classification (переписано с учётом всех 4 статей)

**Понедельник — Финальная иерархия tiers:**

```
TIER S — "True Smart Makers" (primary copy target, 10–30 кошельков)
├── frac_maker_volume ≥ 0.50
├── max_consecutive_5k_plus_months ≥ 9  (Reichenbach)
├── win_rate > 0.55
├── excess_hit_rate > 0.05
├── counterparty_hhi < 0.30
├── wash_score < 0.3  (CRITICAL — Sirolly)
├── NOT in any wash_cluster
├── active_in_current_regime = true
├── last_active ≤ 30 days ago
├── Profitable in ≥ 2 categories
└── Category tier mix: ≥1 HIGH or MED category

TIER C — "Category Specialist Makers" (secondary, 15–40 кошельков)
├── Profitable in ONE specific HIGH-gap category
├── frac_maker_volume в этой категории ≥ 0.50
├── num_trades в категории ≥ 50
├── win_rate в категории > 0.55
├── max_consecutive_5k_plus_months ≥ 6
├── wash_score < 0.3
├── Category tier = HIGH (Media, World Events, Entertainment, Crypto high-gap)
└── Copy ONLY when trading in specialty

TIER A — "Diversified Sharps" (tertiary, 50–150 кошельков)
├── frac_maker_volume ≥ 0.30 OR excess_hit_rate > 0.08
├── max_consecutive_profitable_months ≥ 6
├── total_pnl > $10,000
├── category_hhi < 0.50
├── frac_extreme_price < 0.50
├── counterparty_hhi < 0.40
├── wash_score < 0.4
├── active_in_current_regime = true
└── NOT in wash_cluster

TIER B — "Observe Only"
├── Potentially skilled but insufficient history
├── Single HIGH-gap category specialists with <50 trades
├── Monitor 3 months → promote or demote

TIER X — BLACKLIST (automatic skip)
├── is_suspected_wash = true  (Sirolly)
├── in any wash_cluster
├── wash_score > 0.5
├── counterparty_hhi > 0.50
├── frac_extreme_price > 0.70 AND num_trades < 50
├── Жизнь кошелька <30 дней с extreme PnL
└── PnL/Volume ratio < 0.001 (вероятный wash)
```

**Вторник–Среда:**

- [ ] Автоматический классификатор
- [ ] **Mandatory wash filter применяется перед classification** — любой кошелёк с `is_suspected_wash = true` → Tier X
- [ ] Side preference analysis для Tier S/C
- [ ] Sybil detection supplementary

**Четверг — Out-of-sample validation:**

- [ ] Train: Q4 2024 – Oct 2025
- [ ] Test: Nov 2025 – Apr 2026
- [ ] Persistence by tier
- [ ] **Требования:**
  - Tier S persistence > 70% (благодаря более строгим critеriam)
  - Tier C persistence > 55%
  - Wash-filtered PnL vs non-filtered — должна быть positive delta
- [ ] Sanity check: соответствует ли твой Tier S ~Reichenbach 72/35 кошелькам?

**Пятница:**

- [ ] Финальный watchlist:
  - **10–30 Tier S (True Smart Makers)**
  - **15–40 Tier C (Category Specialists)**
  - **50–150 Tier A (Diversified Sharps)**
  - **Total pool: ~75–220 кошельков**
- [ ] Git tag `v0.3-classification`

### 🚦 Gate 3:

1. Tier S persistence > 70%?
2. Paper PnL с wash filter значимо лучше без filter?
3. Реалистичное количество сигналов (10–30 в день от Tier S+C)?
4. Alpha decay ≤ 30% за 10 мин?

- ✅ → Phase 4
- ❌ 1 → либо порог слишком мягкий, либо test window слишком короткий
- ❌ 2 → wash filter работает неправильно, re-tune threshold
- ❌ 3 → watchlist слишком узкий, добавь Tier A

---

## Phase 4: Real-time Monitoring (Week 6)

### Week 6 — Monitoring & Alerts

**Понедельник:**

- [ ] Polling service
- [ ] Дедупликация
- [ ] MAKER vs TAKER role distinction

**Вторник — Signal classification:**

```
STRONG signal (copy target):
- Tier S OR Tier C (in specialty category)
- MAKER role
- Category tier = HIGH
- Size above median for this wallet
- Counterparty not in wash_cluster
- If longshot price (<0.20 or >0.80): is_optimism_extraction = true

MEDIUM signal:
- Tier S MAKER in MED category
- Tier A MAKER in HIGH category
- Tier S TAKER with urgent indicators (large size, time to resolution short)

WEAK signal (log only):
- Tier A TAKER
- Any signal in LOW category
- Counter-flow bet
- Signal from wallet trading with suspected wash cluster member

SKIP (do not log):
- Any signal where wallet's counterparty is in wash_cluster
- Any signal in market with est_wash_fraction > 0.30
```

**Среда — Telegram alerts:**

```
⚡ STRONG SIGNAL — Tier S Smart Maker (Verified Non-Wash)

Wallet: 0xabc...
Style: Universal Smart Maker
Consistency: 14 consecutive $5k+ months ⭐
Wash Score: 0.08 ✅
Maker Ratio: 68% | Win Rate: 61% | Total PnL: +$487k

Action: POSTED LIMIT BUY — NO @ $0.08
Market: "[Market question]"
Category: World Events (HIGH-gap category)
Size: $5,420 (2.1x wallet median)
Pattern: ⭐ Optimism Tax extraction
Counterparty cleanness: ✅ Verified
Time to resolution: 48h

Recommended copy size: $50 (Kelly fractional)
```

**Четверг:**

- [ ] Dashboard v1
- [ ] Секции: Tier S/C/A, Live signals (color-coded by strength), Category breakdowns, Wash alerts (NEW), PnL history

**Пятница:**

- [ ] Deploy Railway
- [ ] PAPER MODE launch
- [ ] Git tag `v0.4-monitoring-live`

---

## Phase 5: Paper Trading Validation (Week 7)

### Week 7 — Paper Trading

**Каждый день:**

- Сигналы копятся
- Daily review

**Backtest stratification:**

- Tier (S, C, A)
- Role (MAKER vs TAKER)
- Category tier (HIGH, MED, LOW)
- Side pattern (optimism vs counter-flow)
- Signal strength
- **Wash-adjacent vs clean** (NEW) — сделки, где контрпартнёр имеет wash_score > 0.3

**Воскресенье — Review:**

- Лучшие cells по PnL
- Alpha decay curve
- Wash impact: разница PnL когда фильтруем wash-adjacent

### 🚦 Gate 4:

1. Simulated PnL (best cells) > 0 после costs?
2. Wash filter даёт measurable improvement?
3. ≥50 STRONG signals за неделю?
4. Stratification подтверждает гипотезы?

- ✅ → Phase 6
- ❌ → extend paper

---

## Phase 6: Prometheus Integration & Live (Week 8)

### Week 8 — Live Copy Trading

**Понедельник–Вторник:**

- [ ] Copy trade API endpoint в Prometheus
- [ ] Sizing: fractional Kelly × category multiplier
- [ ] Risk management:
  - Max exposure per market: $200
  - Max daily loss: $500 → kill switch
  - Blacklist liquidity <$10k
  - **NEW:** Skip if market's est_wash_fraction > 0.25
  - Skip if >2 active positions on this market

**Среда — Pipeline:**

```
1. Signal detected
2. Wash check (wallet AND counterparty AND market)  ← NEW критический step
3. Tier check (S/C only на старте)
4. Category tier check (HIGH/MED only)
5. Role check (MAKER preferred)
6. Side pattern check
7. Size calc (Kelly × category multiplier)
8. Manual confirm через Telegram (первая неделя)
9. Execute через Prometheus
10. Log everything
```

**Четверг — Soft launch:**

- ONLY STRONG signals
- $10 per signal start
- Manual approval every trade
- Close monitoring

**Пятница:**

- Daily PnL report с breakdown by strength × category × wash-cleanness
- Dashboard: Athena live PnL vs baseline Prometheus
- Git tag `v1.0-live`

---

## Phase 7: Iterate & Scale (Week 9+)

**Еженедельно:**

- Review signals, adjust filters
- **NEW:** Re-run Sirolly wash detection (wash landscape меняется быстро)

**Ежемесячно:**

- Full watchlist refresh
- Category tier recalibration
- **NEW:** Token rumor monitoring — если Polymarket намекает на airdrop, wash activity взорвётся, нужна эскалация wash filtering

**Квартально:**

- ML кластеризация
- Cross-market signals
- Потенциально: own graph-based detector improvements (Sirolly methodology не единственный способ)

**Monetization (Phase 8+):**

Обновлённый narrative с 4-мя peer-reviewed источниками:

> "Четыре независимых академических исследования (Akey et al., Becker, Sirolly et al., Reichenbach & Walther) подтверждают: 70% юзеров Polymarket/Kalshi теряют деньги. 1% забирает 84% прибылей. Только 35 кошельков стабильно зарабатывают >$5k 12 месяцев подряд. 25% volume — wash trading artificat. Athena идентифицирует эти 35 кошельков через network-based wash filtering и автоматически копирует их сигналы в real-time."

Это научно-bulletproof positioning. Ты не "гадаешь" — ты применяешь академически валидированные методологии к production.

**Price points:**

- Free: delayed alerts (для marketing)
- Premium ($200–400/мес): real-time Tier S signals
- Pro ($800–1500/мес): full API, all tiers, wash cluster registry
- Enterprise ($2000+/мес): для funds — data feeds + custom analysis

---

## Definition of Done (Week 12)

- [ ] Live copy-trading 4+ недели без critical bugs
- [ ] Total PnL > 0 после fees/slippage
- [ ] Tier S: 10–30 кошельков, turnover <20%/мес
- [ ] Wash detector runs weekly, flags ~14% wallets
- [ ] Kill switch протестирован
- [ ] STRONG signals доминируют PnL attribution
- [ ] Wash-filtered backtest показывает positive delta
- [ ] 5-минутная демо: detection → wash check → categorization → signal → execution → PnL

---

## Критические риски (обновлённые)

1. **Survivorship bias** — mitigated через out-of-sample + consistency requirement
2. **Alpha decay** — monitored через latency-stratified backtest
3. **Wash trading contamination** — addressed через Sirolly algorithm + re-run weekly
4. **Regime change** — monthly recalibration
5. **Category drift** — quarterly recalibration
6. **Maker limit orders не исполняются** — копируем только filled trades
7. **API changes** — abstraction + monitoring
8. **Regulatory** — серая зона, объёмы низкие
9. **Time budget** — PPF + Prometheus + Athena
10. **NEW: Token airdrop wave** — если Polymarket запустит токен, wash волна может сломать watchlist на недели. Нужен early warning system через monitoring volume без organic triggers.

---

## Референсы (финальный список)

**Core papers:**

- **Akey, Grégoire, Harvie, Martineau (Mar 2026)** — profit concentration, maker-taker, classification
- **Becker (Jan 2026)** — Kalshi microstructure, optimism tax, category variance
- **Sirolly, Ma, Kanoria, Sethi (Nov 2025)** — wash trading detection methodology
- **Reichenbach, Walther (Dec 2025)** — skill persistence, consistency counts

**Supplementary:**

- **Tsang, Yang (2025)** — Polymarket 2024 election microstructure
- **Gomez Cram et al. (2025)** — Polymarket as earnings expectations data
- **Saguillo et al. (2025)** — arbitrage opportunities Polymarket
- **Diercks, Katz, Wright (2026)** — Kalshi macro markets

---

## Ключевые изменения v1.2 → v1.3 (summary)

**Новые модули:**

- `/wash_detector` — реализация Sirolly network-based algorithm
- Wash cluster registry (`wash_clusters`, `wash_cluster_membership`)
- Monthly PnL history (`wallet_monthly_pnl`) для Reichenbach consistency
- Wash contamination tracking per category

**Новые метрики:**

- `wash_score`, `is_suspected_wash`, `wash_cluster_id`
- `max_consecutive_profitable_months`
- `max_consecutive_5k_plus_months` (ключевая Reichenbach метрика)
- `pnl_to_volume_ratio`, `rapid_open_close_ratio`
- `est_wash_fraction` per category

**Новые фильтры:**

- Mandatory wash check в classification pipeline
- Wash check в signal execution pipeline
- Market-level wash check (skip markets с >25% wash)

**Реалистичные ожидания:**

- Tier S: было 30–50, стало **10–30** (True Smart Makers)
- Всего actionable watchlist: **75–220 кошельков**
- Signals per day: **10–30 STRONG**, не сотни

**Peer-validation:**

- От 1 статьи (v1.0) → 2 статьи (v1.2) → **4 статьи (v1.3)**
- Все независимо подтверждают gipoтезу
- Bulletproof foundation для eventual monetization

Поехали.
