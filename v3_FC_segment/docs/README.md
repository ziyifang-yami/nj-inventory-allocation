# NJ FC Segment Simulator — Design Documentation

Real-time Streamlit dashboard for NJ warehouse (wh=002) inventory allocation simulation.

## Architecture

- **Data Sources**: MySQL (real-time inventory, capacity, lot data) + Athena (rolling 30D sales/AOD)
- **Segmentation**: TOP / MIDDLE / TAIL based on Active Order Days + 30D Order Lines thresholds
- **Planning**: Greedy daily schedule with vol/locs balance constraints

## Tabs

| Tab | Purpose |
|-----|---------|
| [Overview](overview.md) | Tier distribution & target comparison |
| [Zone View](zone_view.md) | FC vs SFC inventory by location |
| [Before vs After](before_vs_after.md) | Ideal state comparison |
| [Capacity](capacity.md) | FC/SFC warehouse capacity utilization |
| [Planning](planning.md) | Daily transfer schedule & location plans |
