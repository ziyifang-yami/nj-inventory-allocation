# Tab 1: Overview

**Purpose**: Shows current tier distribution (TOP/MIDDLE/TAIL) and how well the TOP tier meets business targets.

## Data Flow

```mermaid
flowchart TD
    A[MySQL: wh_inventory_transaction] -->|Real-time fc_qty, sfc_qty| B[load_inventory]
    C[Athena: inventory_record + trade_item_sale_agg_daily] -->|Rolling 30D AOD, order_lines| D[load]
    E[CSV fallback: segment_sku_detail_*.csv] -->|If Athena unavailable| D
    B --> F[Merge on item_number]
    D --> F
    F --> G[classify SKUs]

    G -->|AOD ≥ threshold AND OL ≥ threshold| H[TOP]
    G -->|OL < tail_threshold| I[TAIL]
    G -->|else| J[MIDDLE]
```

## Segmentation Rules

```mermaid
flowchart LR
    SKU --> Q1{Active Order Days\n≥ AOD_threshold?}
    Q1 -->|Yes| Q2{Order Lines 30D\n≥ OL_threshold?}
    Q1 -->|No| TAIL
    Q2 -->|Yes| TOP
    Q2 -->|No| Q3{Order Lines 30D\n< tail_threshold?}
    Q3 -->|Yes| TAIL
    Q3 -->|No| MIDDLE
```

## Displayed Metrics

- **Tier cards**: SKU count, OL%, Units%, Volume%
- **Donut charts**: SKU and OL distribution
- **Target bars**: TOP tier vs benchmark (SKU%, OL%, Units%, CBI%)
- **FC vs SFC stacked bar**: Inventory split by tier

## Parameters (Sidebar)

| Parameter | Default | Description |
|-----------|---------|-------------|
| Active Order Days ≥ | 21 | TOP trigger: minimum active selling days in 30D window |
| 30D Order Lines ≥ | 30 | TOP trigger: minimum order lines |
| 30D Order Lines < | 8 | TAIL trigger: low-velocity SKUs |
