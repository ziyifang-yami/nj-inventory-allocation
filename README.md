# NJ Inventory Allocation — v4 DC Allocation

Live dashboard for dual-warehouse inventory management (FC ↔ SFC) at NJ Distribution Center.

**Servers:**
| Port | App | Description |
|------|-----|-------------|
| 8508 | `app.py` | Main dashboard |
| 8512 | `app_test_sfc.py` | SFC→FC test view |
| 8513 | `app_test_fc2sfc.py` | FC→SFC test view |

## Architecture

```
app.py / app_test_*.py   ← Streamlit UI (8508 / 8512 / 8513)
refresh_sfc_cache.py     ← Cron job — rebuilds sfc_cache.db every 30 min
cache_db.py              ← SQLite cache layer (results/sfc_cache.db)
config.py                ← All tunable thresholds (P dict)
logic/
  fc_to_sfc.py           ← FC→SFC: MIDDLE evict + TAIL clear + LOC_CLEAR
  sfc_to_fc.py           ← SFC→FC: T1/T2/T3/T4 task lists
  putaway.py             ← Putaway decision engine (TBD/TOP/MIDDLE/TAIL)
data/
  loader.py              ← MySQL queries (inventory, lots, sales, FC exp)
docs/
  design.md              ← System design
  ui-layout-guide.md     ← UI layout reference
tests/                   ← Unit + property tests
scripts/                 ← One-off debug/deploy scripts (not production)
```

## Key Logic

### Segment Classification
| Segment | Criteria |
|---------|----------|
| TBD | `inv_days_30 < 30` or NaN (new product) |
| TOP | `active_order_days ≥ 21` AND `order_lines_30d ≥ 30` |
| TAIL | `order_lines_30d < 8` |
| MIDDLE | All others |

### FC→SFC Triggers
- **MIDDLE EVICT**: `fc_d2s > 30d` → transfer newest lots to SFC, keep oldest in FC (FIFO)
- **TAIL CLEAR**: any FC stock → full clear to SFC
- **LOC CLEAR**: locations where all live SKUs are in the transfer set

### SFC→FC Task Lists
- **T1**: Emergency (FC D2S < 3d) — all segments
- **T2**: Routine replenishment (FC D2S < 14d) — MIDDLE; all SFC for TOP
- **T3**: Bin→Stock reorganization
- **T4**: Expiry date switch (FEFO)

### Putaway Decision (8504 — Putaway Guide)
See `logic/putaway.py` for the full decision tree.

## Configuration

Key thresholds in `config.py`:

```python
P = {
    "putaway_d2s":         30,   # MIDDLE: FC D2S > this → new goods to SFC
    "mid_evict_d2s":       30,   # FC→SFC trigger
    "mid_keep_d2s":        30,   # FC target keep
    "mid_evict_buffer_d2s": 3,   # FC can drop to keep-buffer after transfer
    "mid_target_d2s":      28,   # SFC→FC replenishment target
    "mid_emergency_d2s":    3,   # T1 emergency trigger
    "mid_replen_d2s":      14,   # T2 routine trigger
    "top_trigger_d2s":     28,   # TOP SFC→FC trigger
}
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DB credentials

# Run dashboard
streamlit run app.py --server.port 8508

# Run cache refresh (also runs as cron every 30 min)
python refresh_sfc_cache.py
```

## Branch History

| Branch | Description |
|--------|-------------|
| `master` | Current production version (v4) |
| `archive/legacy-v1-v3` | Archived: v1 (BU segment), v2 (sales tier), v3 (FC segment), Zone 2 SFC orders |
| `feature/v4-dc-allocation` | Development branch (merged into master) |
