# NJ Inventory Allocation — v4

Dual-warehouse inventory management dashboard for NJ Distribution Center (FC ↔ SFC).  
Runs as three Streamlit apps on the server + one FastAPI putaway guide.

**Live apps:**

| Port | File | Description |
|------|------|-------------|
| 8508 | `app.py` | Main dashboard (Overview, Before/After, FC→SFC, SFC→FC, Putaway) |
| 8512 | `app_test_sfc.py` | SFC→FC task list — test/debug view |
| 8513 | `app_test_fc2sfc.py` | FC→SFC task list — test/debug view |
| 8504 | (warehouse-recommendation repo) | Putaway Guide — scan BT/GT barcode |

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure credentials
cp .env.example .env
# Edit .env: MYSQL_HOST, MYSQL_USER, MYSQL_PASS, AWS_PROFILE (for Athena)

# 3. Run the main dashboard
streamlit run app.py --server.port 8508

# 4. Build / refresh the cache (also runs automatically every 30 min via cron)
python refresh_sfc_cache.py
```

---

## Repository Structure

```
nj-inventory-allocation/
│
├── app.py                   Main Streamlit dashboard (port 8508)
├── app_test_sfc.py          SFC→FC test view (port 8512)
├── app_test_fc2sfc.py       FC→SFC test view (port 8513)
├── cache_db.py              SQLite cache layer — all reads/writes to sfc_cache.db
├── config.py                All tunable thresholds (P dict)
├── refresh_sfc_cache.py     Cron job — rebuilds sfc_cache.db every 30 min
├── requirements.txt         Python dependencies
│
├── logic/                   Core business logic
│   ├── fc_to_sfc.py         FC→SFC: MIDDLE evict, TAIL clear, LOC_CLEAR selection
│   ├── sfc_to_fc.py         SFC→FC: T1/T2/T3/T4 task list builder
│   ├── putaway.py           Putaway decision engine (TBD/TOP/MIDDLE/TAIL)
│   └── classify.py          SKU segment classification helpers
│
├── data/
│   └── loader.py            All MySQL + Athena queries (inventory, lots, sales, exp)
│
├── planning/
│   ├── ideal.py             Ideal inventory state calculator
│   └── schedule.py          30-day rolling schedule
│
├── tests/                   Unit + property-based tests (pytest + Hypothesis)
│   ├── test_classify_*.py   Segment classification tests
│   ├── test_fc_to_sfc_*.py  FC→SFC logic tests
│   ├── test_sfc_to_fc_*.py  SFC→FC logic tests
│   ├── test_putaway_*.py    Putaway decision tests
│   └── test_loader.py       Data loader tests
│
├── docs/                    Documentation
│   ├── LOGIC.md             ← START HERE: segment logic, data sources, transfer rules
│   ├── design.md            Detailed design spec (Chinese): glossary, DB fields, properties
│   └── ui-layout-guide.md   Dashboard UI layout reference
│
├── scripts/                 One-off debug / deploy / diagnostic scripts (not production)
│   ├── deploy_*.py          Deploy scripts for uploading to server
│   ├── check_*.py           Data validation scripts
│   ├── debug_*.py           Debugging scripts
│   └── *.sh                 Shell helpers
│
└── results/                 Runtime output (gitignored except .gitkeep)
    ├── sfc_cache.db         SQLite cache — rebuilt by refresh_sfc_cache.py
    └── athena_cache.csv     Athena sales data cache (12h TTL)
```

---

## Key Concepts

### Segment Classification

| Segment | Condition | Transfer behavior |
|---------|-----------|-------------------|
| **TBD** | `inv_days_30 < 30` or no Athena data | No transfers — hold until classified |
| **TOP** | `active_order_days ≥ 21` AND `order_lines_30d ≥ 100` | All SFC → FC always |
| **TAIL** | `order_lines_30d < 4` | All FC → SFC (full clear) |
| **MIDDLE** | Everything else | D2S-driven bidirectional transfers |

> ⚠️ Thresholds in `refresh_sfc_cache.py` are hardcoded (`OL_TOP=100, OL_TAIL=4`).  
> `config.py` values (`ol_top=30, ol_tail=8`) are used by the UI simulator only.

### FC D2S (Days-to-Sell)
```
fc_d2s = fc_qty / avg_daily_orders
```

### Transfer Triggers
| Direction | Trigger | Rule |
|-----------|---------|------|
| FC → SFC | MIDDLE: `fc_d2s > 30d` | Transfer newest lots to SFC, keep oldest in FC |
| FC → SFC | TAIL: `fc_qty > 0` | Full clear |
| SFC → FC | TOP: `sfc_qty > 0` | All SFC → FC |
| SFC → FC | MIDDLE: `fc_d2s < 14d` | Replenish FC to 28d target |
| SFC → FC | MIDDLE: `fc_d2s < 3d` | Emergency T1 |

### Cache Refresh Cycle

`refresh_sfc_cache.py` runs every 30 min:

```
[1] Inventory (MySQL, live)
[2] Sales data (Athena, 12h TTL)
[3] EXP flags (MySQL, 12h TTL)
[4] Segment classification → SQLite
[5] SFC lot data (MySQL, live)
[6] FC expiry summary (MySQL, 1h TTL)
[7] Build T1-T4 task lists → SQLite
[8] FC→SFC tasks (MySQL, 1h TTL)
[9] Pending putaway (MySQL, 30min TTL)
```

---

## Configuration (`config.py`)

| Parameter | Value | Description |
|-----------|-------|-------------|
| `mid_evict_d2s` | 30 | MIDDLE FC→SFC trigger (fc_d2s above this) |
| `mid_evict_buffer_d2s` | 3 | FC can drop to keep_d2s−3 after transfer |
| `mid_target_d2s` | 28 | SFC→FC replenishment target |
| `mid_emergency_d2s` | 3 | T1 emergency trigger |
| `mid_replen_d2s` | 14 | T2 routine trigger |
| `putaway_d2s` | 30 | Putaway MIDDLE threshold |
| `top_trigger_d2s` | 28 | TOP SFC→FC trigger |

---

## Documentation

| File | Contents |
|------|----------|
| `docs/LOGIC.md` | **Operational reference** — segment logic, all data sources, transfer rules, putaway decision tree, file map. Start here. |
| `docs/design.md` | **Design specification (Chinese)** — full glossary, DB field validation, correctness properties, test strategy |
| `docs/ui-layout-guide.md` | Dashboard UI layout guide |

---

## Branch History

| Branch | Description |
|--------|-------------|
| `master` | Production (v4 DC Allocation) |
| `archive/legacy-v1-v3` | v1 BU segment · v2 Sales tier · v3 FC segment · Zone 2 SFC orders |
| `feature/v4-dc-allocation` | v4 development history |
