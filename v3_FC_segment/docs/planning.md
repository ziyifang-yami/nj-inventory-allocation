# Tab 5: Planning

**Purpose**: Generates a day-by-day transfer schedule respecting daily workforce capacity, with location-level operation plans.

## Overall Architecture

```mermaid
flowchart TD
    A[Segmented SKU DataFrame] --> B[load_sku_loc_stats\nMySQL: total_vol + loc_count per SKU]
    B --> C[run_planning]
    C --> D[Daily Schedule df_sched]
    D --> E[Charts + KPI Cards]
    D --> F[Generate Location Plans\nMySQL: actual FC/SFC locations]
    F --> G[FC→DC Location Table\nday + area density sort]
    F --> H[DC→FC Location Table\nday + area density sort]
```

## Daily Schedule Algorithm

```mermaid
flowchart TD
    Start([Day loop]) --> DC_FC

    subgraph DC_FC ["Step 1: DC→FC (demand side)"]
        T1[Fill cap_in with TOP\nsorted: d2s asc]
        T1 --> T2{TOP remaining?}
        T2 -->|Yes continue| T1
        T2 -->|Done| M1[Fill remaining cap_in with MIDDLE\nsorted: d2s ≤ routine, sfc > 0, needed > 0]
    end

    DC_FC --> FC_DC

    subgraph FC_DC ["Step 2: FC→DC (supply side — balance DC→FC)"]
        direction TB
        U1[Greedy merge: TAIL + MIDDLE evict\nboth pre-sorted by total_vol desc]
        U1 --> U2{day_vol_out ≥ day_vol_in\nAND day_locs_out ≥ day_locs_in?}
        U2 -->|Not yet, need_vol or need_locs| U3{next_mid_vol ≥ next_tail_vol?}
        U3 -->|Yes, insert MIDDLE| U1
        U3 -->|No, take TAIL| U1
        U2 -->|Satisfied, fill remaining cap| U4[Continue TAIL then MIDDLE\nuntil cap_out reached]
        U4 --> U5[Re-sort today's batch by vol desc\nfor operational efficiency]
    end

    FC_DC --> Rec[Record daily batch\nAppend to df_sched]
    Rec --> Brk{All 4 lists exhausted?}
    Brk -->|No| Start
    Brk -->|Yes| End([Done])
```

## TAIL Pre-Sorting (Real-time MySQL)

```mermaid
flowchart LR
    MySQL[wh_lot_inventory\nim_item → xysc_wearhouse_goods] -->|qty × volume per SKU| Stats[total_vol, loc_count]
    Stats --> Sort[TAIL sorted:\n1. total_vol desc\n2. loc_count desc]
    Stats --> Sort2[MIDDLE evict sorted:\n1. total_vol desc\n2. loc_count desc]
```

## FC Location Balance Constraint

```
day_locs_released = s_out (FC→DC locs today)
day_vol_released  = vol_out (FC→DC vol today)

DC→FC allowed only when:
  day_locs_in ≤ day_locs_released
  day_vol_in  ≤ day_vol_released
```

## MIDDLE FC→DC Location Selection Rules

```mermaid
flowchart TD
    SKU[MIDDLE SKU with excess] --> Q1{need_minimal_exp = 1?}

    Q1 -->|Yes| Q2{loc_count > 1?}
    Q2 -->|Yes| R3[Rule 3: Exp + Multi-Loc\nSort by exp_date DESC\nTransfer farthest-date first\nFC keeps nearest-date stock]
    Q2 -->|No| R4[Rule 4: Exp + Single-Loc\nTransfer excess only\nkeep = adl × 30D]

    Q1 -->|No| Q3{loc_count > 1?}
    Q3 -->|Yes| R1[Rule 1: No-Exp + Multi-Loc\nFind location closest to excess qty\nIf within 15%: transfer whole location\nElse: accumulate smallest locations]
    Q3 -->|No| R2[Rule 2: No-Exp + Single-Loc\nTransfer whole location\nkeep = adl × 30D]

    R2 --> Guard{transfer_qty ≥ 1 location?}
    Guard -->|No| Skip[Stay in FC]
    Guard -->|Yes| Transfer
```

## Parameters

| Parameter | Description |
|-----------|-------------|
| Workers FC→DC | Daily FC→DC headcount |
| SKU/person/day (FC→DC) | Productivity rate |
| Workers DC→FC | Daily DC→FC headcount |
| SKU/person/day (DC→FC) | Productivity rate |
| Tasks/person/day | Physical task limit |
| Pallets/task | Pallets handled per task |
| TOP trigger D2S | FC D2S below this → replenish from SFC |
| TOP target D2S | Target days of supply after replenish |
| MID routine D2S | MIDDLE replenish trigger |
| MID evict D2S | MIDDLE evict trigger (too much in FC) |
| Min loc qty | Skip transfer if excess < this value |

## Location Plan Generation

After schedule is computed, clicking **Generate Daily Location Plans**:

1. Extract SKUs per day from `df_sched`
2. MySQL: query actual FC lot locations (`wh_lot_inventory`) for FC→DC SKUs
3. MySQL: query actual SFC locations (`wh_inventory_transaction`) for DC→FC SKUs
4. Sort each day's locations by: **area density (prefix count desc) → location_no asc**
5. Output two tables with Day filter, SKU search, download CSV
