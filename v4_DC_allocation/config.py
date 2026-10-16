# config.py — All tunable parameters for NJ Inventory Allocation v4
#
# Import this module and reference P["param_name"] throughout the codebase.
# The Threshold Simulator in the Streamlit UI passes overridden values
# directly to classifier/engine functions; it does NOT modify this file.
#
# Last updated: 2026-10-01

P = {
    # --- Segmentation thresholds ---
    "aod_top": 21,       # Active Order Days minimum for TOP segment
    "ol_top":  30,       # Order Lines (30d) minimum for TOP segment
    "ol_tail":  8,       # Order Lines (30d) below which SKU is TAIL (default 8, tunable)

    # --- FC→SFC eviction ---
    "mid_evict_d2s":       30,  # MIDDLE FC D2S above which FC→SFC transfer triggers
    "mid_keep_d2s":        30,  # MIDDLE FC target keep D2S (ceiling rule)
    "mid_evict_buffer_d2s": 3,  # Buffer: FC can drop to (mid_keep_d2s - buffer) after transfer
                                # i.e. min_keep = adl × (mid_keep_d2s - mid_evict_buffer_d2s)
                                # Set to 0 to require exact keep_target after each location.

    # --- SFC→FC replenishment ---
    "mid_target_d2s":    28,  # MIDDLE SFC→FC补货目标 D2S（箱规取整基准，可调）
    "mid_emergency_d2s":  3,  # T1紧急触发线：fc_d2s < 3 → 立即补货
    "mid_replen_d2s":    14,  # T2常规触发线：fc_d2s < 14 → 常规补货

    # --- TOP SFC→FC ---
    "top_trigger_d2s": 28,   # TOP: fc_d2s 低于此值才触发 SFC→FC

    # --- Putaway ---
    "putaway_d2s": 30,   # MIDDLE: FC D2S > 此值 → 新货入 SFC (matches mid_evict_d2s)

    # --- Operational constraints ---
    "min_loc_qty": 5,    # 单库位最小转移量（低于此值库位跳过）
}
