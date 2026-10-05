# Tab 3: Before vs After

**Purpose**: Shows the ideal state if all allocation rules were fully executed — comparing current inventory distribution vs the optimized target state.

## Ideal State Calculation (calc_ideal)

```mermaid
flowchart TD
    A[All in-stock SKUs] --> B{Segment?}

    B -->|TOP| C{FC D2S < top_trigger?}
    C -->|Yes| D[DC_TO_FC\ntransfer = min needed, sfc_qty\nPriority: d2s low → vol large → adl high]
    C -->|No| E[KEEP]

    B -->|MIDDLE| F{FC D2S?}
    F -->|≤ mid_routine| G{sfc > 0 AND needed > 0?}
    G -->|Yes| H[DC_TO_FC\nfill to 28D target\nPriority: d2s×1e6 − vol×1e3 − adl]
    G -->|No| E
    F -->|> mid_evict| I{excess > min_qty?}
    I -->|Yes| J[FC_TO_DC\nmove excess out\nPriority: −vol×1e3 − excess]
    I -->|No| E

    B -->|TAIL| K[FC_TO_DC\nfull clear fc_qty\nPriority: 100]
```

## Key Outputs

- `fc_ideal` / `sfc_ideal`: inventory after all transfers
- `fc_vol_ideal` / `sfc_vol_ideal`: volume after transfers
- `priority`: used for scheduling order

## Displayed Sections

1. **Overall Inventory Shift**: dual animated arrow diagram showing FC→SFC and SFC→FC movements with qty/vol
2. **By Segment cards**: per-tier SKU location changes (FC/SFC/Overlap) + qty + volume
3. **SKU Distribution bar**: before/after grouped bars
4. **SKU-level changes table**: filterable by tier and action

## Note on Ideal vs Plan

- **Before vs After** = unconstrained ideal state (infinite time, no daily cap)
- **Planning tab** = constrained by daily worker capacity
- Gap between the two = what requires extended timeline or more resources
