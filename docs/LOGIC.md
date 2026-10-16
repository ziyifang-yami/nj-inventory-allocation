# NJ Inventory Allocation v4 — System Logic

**Last updated:** 2026-10-14  
**Live dashboards:** 8508 (main) · 8512 (SFC→FC test) · 8513 (FC→SFC test)  
**Putaway Guide:** 8504

---

## 1. Data Sources

### 1a. MySQL (real-time / 30-min cache)

| Database | Table | Used For |
|----------|-------|----------|
| `yamibuy_wh` | `wh_storage_location_item` | SKU-level inventory per location |
| `yamibuy_wh` | `wh_inventory_transaction` | Live quantities |
| `yamibuy_wh` | `wh_storage_location` | Zone, location type, size |
| `yamibuy_wh` | `wh_location_size` | Location dimensions |
| `yamibuy_wh` | `wh_lot_inventory` | Lot-level FC/SFC stock |
| `yamibuy_wh` | `wh_lot_master` | Lot receive timestamps (inv_age_days) |
| `yamibuy_wh` | `wh_inbound_batch` | Expiry dates, inbound times |
| `yamibuy_wh` | `wh_inventory_transaction_log` | PO references for pending putaway |
| `yamibuy_im` | `im_item_extend` | `need_minimal_exp` flag (expiry tracking) |
| `yamibuy_im` | `im_item` | SKU→goods_id mapping |
| `yamibuy_master` | `xysc_wearhouse_goods` | Unit volume (cbm) |

**Filters applied to all inventory queries:**
- `warehouse_number = '002'` (NJ warehouse only)
- `location_type IN (3, 4)` (Stock=3, Bin=4 — excludes receiving/staging)
- `size_id NOT IN (13, 33)` (excludes oversized/special locations)
- `business_type != 8` (excludes FBY items from FC/SFC allocation)
- `zone_id = 2` → **SFC**; `zone_id != 2` → **FC**

### 1b. AWS Athena (12-hour cache → `results/athena_cache.csv`)

| Database | Table | Fields Used | History |
|----------|-------|-------------|---------|
| `yamibuy_inventory` | `inventory_record` | `inv_days_30` — days SKU had stock in last 30d | From **2018-01-06** |
| `dwb_trade` | `trade_item_sale_agg_daily` | `active_order_days`, `order_lines_30d`, `avg_daily_orders`, `sales_units_30d` | From **2017-10-12** |

> Both tables cover NJ warehouse 002 only, filtered to item_number starting with `1%` or `5%`.  
> Verified 2026-10-16: `inventory_record` has 270,563 distinct SKUs across 3,200 days; `trade_item_sale_agg_daily` has 130,533 SKUs across 2,299 days.

**Athena query logic:**
- `inv_days_30`: count of the most recent 30 calendar days on which the SKU had `available_qty > 0` (out-of-stock days excluded)
- `active_order_days`: number of days within those same 30 in-stock days on which actual orders were placed — joined on `day_id = inv_date`, so only orders on in-stock days count
- `order_lines_30d`: total order lines across those 30 in-stock days
- `avg_daily_orders`: `order_lines_30d / 30.0`

### 1c. SQLite Cache (`results/sfc_cache.db`)

Rebuilt every 30 min by `refresh_sfc_cache.py`. Tables:

| Table | Contents | TTL |
|-------|----------|-----|
| `sales` | Athena sales metrics per SKU | 12h |
| `sfc_lots` | SFC lot locations (location-level) | every run |
| `fc_lots` | FC lot locations (location-level) | on-demand |
| `fc_exp` | FC min/max expiry per SKU | 1h |
| `exp_flags` | SKUs with `need_minimal_exp=1` | 12h |
| `tbd_skus` | TBD-classified SKUs | every run |
| `task_list` | T1/T2/T3/T4 SFC→FC tasks | every run |
| `fc2sfc_tasks` | FC→SFC tasks (LOC/MIDDLE/TAIL) | 1h |
| `fc2sfc_triggers` | SKU-level trigger summary | every run |
| `pending_putaway` | RECG staging items + BT/GT | 30 min |
| `meta` | Last-updated timestamps | — |

---

## 2. Segment Classification

**Code:** `refresh_sfc_cache.py` Steps 4 / 4b  
**Applies to:** All SKUs with live FC or SFC inventory in warehouse 002

### Step 1 — Classify by sales data

All classification metrics are derived from the same rolling window: **the most recent 30 calendar days on which the SKU had available inventory** (`available_qty > 0`). Out-of-stock days are excluded from this window. For new products with fewer than 30 in-stock days in history, the window is smaller.

Within this window:
- `inv_days_30` — count of those in-stock days (max 30; < 30 = new product → TBD)
- `active_order_days` — number of days within those same 30 in-stock days on which actual orders were placed (always ≤ `inv_days_30`)
- `order_lines_30d` — total order lines across those 30 in-stock days
- `avg_daily_orders` — `order_lines_30d / 30`

```python
# Thresholds — read from config.P (aligned 2026-10-16)
AOD_TOP  = P["aod_top"]   # 21   active_order_days >= 21
OL_TOP   = P["ol_top"]    # 30   order_lines_30d  >= 30
OL_TAIL  = P["ol_tail"]   #  8   order_lines_30d  < 8

seg = "TOP"    if active_order_days >= 21 AND order_lines_30d >= 30
    = "TAIL"   if order_lines_30d < 8
    = "MIDDLE" otherwise
```

### Step 2 — Override with TBD

```python
# TBD: the SKU has not yet accumulated 30 in-stock days in its history
# inv_days_30 < 30 means this is a new product — fewer than 30 calendar days
# with available_qty > 0 have been recorded. Insufficient data to classify.
# inv_days_30 = NaN means the SKU has no Athena record at all (never been in stock).
tbd_mask = inv_days_30 is NaN
        OR inv_days_30 < 30

if tbd_mask:
    seg = "TBD"  # overrides TOP/MIDDLE/TAIL
```

> **Practical implication:** Since `inventory_record` goes back to 2018, any SKU with at least 30 historical in-stock days will have `inv_days_30 = 30` and be classifiable. Only genuinely new products (first inbound < 30 in-stock days ago) will be TBD.

### Segment Summary Table

| Segment | Primary Condition | Override |
|---------|-------------------|---------|
| **TBD** | `inv_days_30 < 30` or NaN | Applied last — overrides all |
| **TOP** | `active_order_days ≥ 21` AND `order_lines_30d ≥ 30` | — |
| **TAIL** | `order_lines_30d < 8` | — |
| **MIDDLE** | Everything else | — |

### Data fields used in classification

All fields below are computed over the **rolling window of the most recent 30 in-stock days** (calendar days where `available_qty > 0`; out-of-stock days excluded; not necessarily consecutive):

| Field | Source | Description |
|-------|--------|-------------|
| `inv_days_30` | Athena `inventory_record` | Count of those in-stock days (< 30 = new product → TBD) |
| `active_order_days` | Athena `trade_item_sale_agg_daily` | Days within those same 30 in-stock days on which actual orders were placed (always ≤ `inv_days_30`) |
| `order_lines_30d` | Athena `trade_item_sale_agg_daily` | Total order lines across those 30 in-stock days |
| `avg_daily_orders` | Computed: `order_lines_30d / 30` | Used for D2S calculations |

---

## 3. FC Days-to-Sell (D2S)

```python
fc_d2s = fc_qty / avg_daily_orders   # NaN if avg_daily_orders = 0
```

> **Note on TAIL with `avg_daily_orders = 0`:** TAIL includes SKUs where `order_lines_30d = 0` (no sales in the rolling window). This covers: (1) zero-sales SKUs with live inventory, (2) SKUs missing from Athena sales data (filled as 0 after merge). These SKUs will still be fully cleared from FC → SFC, but SFC→FC transfers won't happen since `needed = 0 × 28 - fc_qty = 0`.

Used as the primary trigger for all transfer decisions.

---

## 4. Transfer Decision Logic

### 4a. FC→SFC (eviction)

**Code:** `logic/fc_to_sfc.py` · `refresh_sfc_cache.py` Step 8b

| Trigger | Condition | Rule |
|---------|-----------|------|
| **MIDDLE_EVICT** | MIDDLE SKU, `fc_d2s > 30d` | Transfer excess from FC to SFC |
| **TAIL_CLEAR** | TAIL SKU, `fc_qty > 0` | Full clear — all FC stock to SFC |
| **LOC_CLEAR** | Location where every live SKU is in MIDDLE_EVICT or TAIL_CLEAR set | Vacate the location entirely |

**MIDDLE EVICT quantity logic:**
```
keep_qty   = ceil(avg_daily_orders × 30)
min_keep   = avg_daily_orders × (30 - 3)   # 3d buffer from config
excess_qty = fc_qty - min_keep              # max transferable
```

**Location selection (MIDDLE EVICT):**
- Only whole locations (no partial splits)
- Only locations where `loc_qty ≤ excess_qty`
- **Has expiry:** `exp_date ASC` → `Stock first, then Bin` → `inv_age_days ASC` → `qty DESC`
- **No expiry:** `Stock first, then Bin` → `inv_age_days ASC` → `qty DESC`
  - `inv_age_days ASC` = newest stock first → newest goes to SFC, oldest stays in FC (FIFO at FC)
- Greedy selection until cumulative qty ≥ excess_qty

### 4b. SFC→FC (replenishment)

**Code:** `logic/sfc_to_fc.py` · `refresh_sfc_cache.py` Steps 5-7

#### TOP SKUs
- **Trigger:** `sfc_qty > 0` (always)
- **T1 (Emergency):** `fc_d2s < 3d` → greedy whole-location to target_d2s=28
- **T2 (Routine):** `fc_d2s ≥ 3d` → transfer ALL SFC stock to FC
- **T4 (EXP):** SFC lot expiry < FC min expiry → transfer bad lots

#### MIDDLE SKUs
| Task | Condition | Action |
|------|-----------|--------|
| **T1** | `fc_d2s < 3d` (emergency) | All SFC → greedy to target D2S |
| **T1** | EXP: SFC Bin worse than FC | Transfer bad Bin lots |
| **T2** | `3d ≤ fc_d2s < 14d`, Stock only | Greedy to target D2S=28 |
| **T3** | SFC has Bin with good EXP | Bin→Stock reorganization |
| **T4** | EXP: SFC Stock worse than FC | Transfer bad Stock lots |

**Global sort for T1/T2:** `fc_d2s ASC → order_lines_30d DESC → avg_daily_orders DESC`  
**Within-page sort:** `location_no ASC` (only in paginated view, not All)

**T2 transfer quantity:**
```
needed = avg_daily_orders × target_d2s(28) - fc_qty
if sfc_total ≤ needed → take all
else → greedy whole-location until cumulative ≥ needed
```

**Location selection sort for T1/T2 (MIDDLE and TOP):**

| Segment | Has expiry | Sort order |
|---------|-----------|------------|
| MIDDLE T1/T2 | Yes | `exp_date ASC` → `Stock first, then Bin` → `inv_age_days DESC` → `qty DESC` |
| MIDDLE T1/T2 | No | `Stock first, then Bin` → `inv_age_days DESC` → `qty DESC` |
| TOP | Yes | `exp_date ASC` → `inv_age_days DESC` → `qty DESC` |
| TOP | No | `Stock first, then Bin` → `inv_age_days DESC` → `qty DESC` |

`inv_age_days DESC` = oldest SFC stock first → oldest goes back to FC first (FIFO)

---

### 4c. Putaway Decision

**Code:** `logic/putaway.py` · **App:** 8504 (Putaway Guide)  
**Input:** Scan BT (`BT{rec_Id}`) or GT (`GT…`) → auto-resolves SKU

```
TBD    → Keep in Current Staging Area  (wait for next classification cycle)
TOP    → FC                             (TOP always lives in FC)
TAIL   → SFC                            (TAIL always lives in SFC)

MIDDLE (in order):
  1. need_exp=1 AND new_exp < fc_min_exp          → FC   (MID_EXP_EARLIER_FC, FEFO)
  2. fc_d2s > 30d                                  → SFC  (MID_FC_D2S_HIGH)
  3. sfc_qty > 0:
     a. need_exp=1 AND new_exp ≤ sfc_min_exp       → FC   (MID_SFC_EXP_EARLIER_FC, FEFO)
     b. otherwise                                   → SFC  (MID_SFC_HAS_STOCK)
  4. sfc_qty = 0                                   → FC   (MID_SFC_EMPTY)
```

**Expiry data for putaway decisions:**
- `need_minimal_exp`: real-time from `yamibuy_im.im_item_extend`
- `fc_min_exp_date`: MIN expiry across all FC lots (real-time)
- `sfc_min_exp_date`: MIN expiry across all SFC lots (real-time)

---

## 5. Configuration Parameters

**File:** `config.py` → `P` dict

| Parameter | Value | Used By |
|-----------|-------|---------|
| `aod_top` | 21 | TOP: active_order_days threshold (used by both refresh and UI) |
| `ol_top` | 30 | TOP: order_lines_30d threshold |
| `ol_tail` | 8 | TAIL: order_lines_30d below this |
| `mid_evict_d2s` | 30 | FC→SFC trigger |
| `mid_keep_d2s` | 30 | FC→SFC keep target |
| `mid_evict_buffer_d2s` | 3 | FC→SFC buffer |
| `mid_target_d2s` | 28 | SFC→FC replenishment target |
| `mid_emergency_d2s` | 3 | T1 trigger |
| `mid_replen_d2s` | 14 | T2 trigger |
| `top_trigger_d2s` | 28 | TOP SFC→FC trigger |
| `putaway_d2s` | 30 | Putaway MIDDLE threshold |
| `min_loc_qty` | 5 | Minimum location qty to include |

---

## 6. Refresh Cycle

```
Every 30 minutes (cron → refresh_sfc_cache.py):

  [1] Inventory (MySQL, always fresh)     → df_inv
  [2] Sales (Athena, 12h TTL)             → df_sales → SQLite sales table
  [3] EXP flags (MySQL, 12h TTL)          → SQLite exp_flags table
  [4] Segment classification              → TOP/MIDDLE/TAIL
  [4b] TBD override                       → SQLite tbd_skus table
  [5] SFC lot data (MySQL, always fresh)  → SQLite sfc_lots table
  [6] FC expiry summary (MySQL, 1h TTL)   → SQLite fc_exp table
  [7] Build T1-T4 task lists              → SQLite task_list table
  [8a] FC→SFC triggers (SKU level)        → SQLite fc2sfc_triggers table
  [8b] FC→SFC tasks (lot level, 1h TTL)   → SQLite fc2sfc_tasks table
  [9] Pending putaway (MySQL, 30min TTL)  → SQLite pending_putaway table
```

---

## 7. File Map

```
nj-inventory-allocation/
├── app.py                  ← Main dashboard (8508)
├── app_test_sfc.py         ← SFC→FC test view (8512)
├── app_test_fc2sfc.py      ← FC→SFC test view (8513)
├── cache_db.py             ← SQLite cache layer (all read/write)
├── config.py               ← All tunable thresholds (P dict)
├── refresh_sfc_cache.py    ← Cron job — rebuilds sfc_cache.db
│
├── logic/
│   ├── fc_to_sfc.py        ← FC→SFC: MIDDLE evict, TAIL clear, LOC_CLEAR
│   ├── sfc_to_fc.py        ← SFC→FC: T1/T2/T3/T4 task lists
│   ├── putaway.py          ← Putaway decision engine (8504)
│   └── classify.py         ← (placeholder — segmentation is in refresh_sfc_cache.py)
│
├── data/
│   └── loader.py           ← All MySQL + Athena queries
│
├── docs/
│   ├── LOGIC.md            ← This file
│   ├── design.md           ← System architecture
│   └── ui-layout-guide.md  ← Dashboard UI reference
│
├── tests/                  ← Unit + property tests
├── scripts/                ← One-off debug/deploy scripts (not production)
├── planning/               ← ideal.py, schedule.py
│
├── results/                ← Runtime (gitignored)
│   ├── sfc_cache.db        ← SQLite cache
│   └── athena_cache.csv    ← Athena query cache
│
└── .env                    ← DB credentials (gitignored)
```

---

## 8. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Location-level GROUP BY (not lot-level) | One row per location prevents duplicate transfer tasks for same location |
| FC→SFC: `inv_age_days ASC` (newest first) | Newest stock goes to SFC; oldest stays in FC for FIFO outbound |
| SFC→FC: `inv_age_days DESC` (oldest first) | Oldest SFC stock returns to FC first; ensures FIFO across the dual-warehouse cycle |
| `zone_id != 2` = FC (not `zone_id = 1`) | Covers all non-SFC zones including transit/overflow |
| TBD excludes from all transfers | Insufficient data — hold until next Athena cycle classifies it |
| 3-day buffer on FC eviction | Avoids immediate re-transfer after eviction; keeps D2S between 27-30d |
| Whole-location transfers only | Simplifies WMS operations — no partial location picks |
