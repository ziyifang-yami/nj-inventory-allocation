"""
V3 FC Segment — Threshold Simulation
Run: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

def _load_env():
    """Auto-detect .env: current dir first, then parent."""
    from dotenv import load_dotenv
    from pathlib import Path
    env = Path(".") / ".env"
    if not env.exists():
        env = Path("..") / ".env"
    load_dotenv(dotenv_path=env)

st.set_page_config(
    page_title="NJ Segment Simulator",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global style ─────────────────────────────────────────────
st.markdown("""
<style>
  /* font */
  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

  /* hide default header padding */
  .block-container { padding-top: 1.5rem; }

  /* metric cards */
  .seg-card {
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 8px;
    color: #fff;
  }
  .seg-card h2 { margin: 0 0 4px 0; font-size: 1rem; font-weight: 500; opacity: .85; }
  .seg-card .big { font-size: 2rem; font-weight: 700; margin: 0; }
  .seg-card .sub { font-size: .85rem; opacity: .8; margin-top: 6px; }
  .seg-card .row { display: flex; gap: 20px; margin-top: 12px; }
  .seg-card .stat { flex: 1; }
  .seg-card .stat-label { font-size: .7rem; opacity: .7; text-transform: uppercase; letter-spacing: .04em; }
  .seg-card .stat-val   { font-size: 1.1rem; font-weight: 600; }

  /* target bar */
  .target-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
  .target-label { width: 70px; font-size: .8rem; color: #666; }
  .bar-wrap { flex: 1; background: #f0f0f0; border-radius: 20px; height: 8px; }
  .bar-fill  { height: 8px; border-radius: 20px; }
  .target-val { width: 50px; font-size: .8rem; font-weight: 600; text-align: right; }
  .gap-badge { font-size: .75rem; padding: 1px 7px; border-radius: 99px; }

  /* sticky tabs */
  div[data-testid="stTabs"] > div:first-child {
    position: sticky;
    top: 0;
    z-index: 999;
    background: white;
    padding: 8px 0 4px 0;
    border-bottom: 1px solid #eee;
  }
</style>
""", unsafe_allow_html=True)

# ── Palette ───────────────────────────────────────────────────
COLORS = {
    "TOP":    "#5B8DEF",   # blue
    "MIDDLE": "#F0A500",   # amber
    "TAIL":   "#8C8C8C",   # grey
}
TARGETS = {"SKU %": 10.2, "OL %": 70.2, "Units %": 77.7, "CBI %": 80.4}

# ── Load ─────────────────────────────────────────────────────
@st.cache_data(ttl=300)   # 5分钟缓存 — 实时库存
def load_inventory():
    """Real-time FC/SFC inventory from MySQL — overwrites CSV qty."""
    import os
    from pathlib import Path
    try:
        _load_env()
        import pymysql
        conn = pymysql.connect(
            host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
            connect_timeout=8,
        )
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    wit.item_number,
                    SUM(CASE
                        WHEN wit.location_no NOT LIKE 'S%%'
                         AND wit.location_no NOT LIKE 'DC%%'
                         AND wit.location_no NOT LIKE 'K%%'
                        THEN wit.quantity ELSE 0 END) AS fc_qty,
                    SUM(CASE
                        WHEN wit.location_no     LIKE 'S%%'
                          OR wit.location_no     LIKE 'DC%%'
                          OR wit.location_no     LIKE 'K%%'
                        THEN wit.quantity ELSE 0 END) AS sfc_qty
                FROM yamibuy_wh.wh_inventory_transaction wit
                JOIN yamibuy_wh.wh_storage_location wsl
                    ON wsl.location_no      = wit.location_no
                   AND wsl.warehouse_number = wit.warehouse_number
                WHERE wit.warehouse_number = '002'
                  AND wit.quantity > 0
                  AND wsl.location_type IN (3, 4)
                  AND (wit.item_number LIKE '1%%' OR wit.item_number LIKE '5%%')
                GROUP BY wit.item_number
                HAVING fc_qty > 0 OR sfc_qty > 0
            """)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()
        df_inv = pd.DataFrame(rows, columns=cols)
        df_inv["item_number"] = df_inv["item_number"].astype(str)
        df_inv["fc_qty"]  = pd.to_numeric(df_inv["fc_qty"],  errors="coerce").fillna(0)
        df_inv["sfc_qty"] = pd.to_numeric(df_inv["sfc_qty"], errors="coerce").fillna(0)
        return df_inv, None
    except Exception as e:
        return None, str(e)

@st.cache_data(ttl=1800)  # 30分钟自动刷新
def load_capacity():
    """实时从 MySQL 读取 FC/SFC 容量数据"""
    import os
    from pathlib import Path
    try:
        _load_env()
        import pymysql
        conn = pymysql.connect(
            host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
            connect_timeout=8,
        )
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    CASE
                        WHEN wsl.location_no NOT LIKE 'S%'
                         AND wsl.location_no NOT LIKE 'DC%'
                         AND wsl.location_no NOT LIKE 'K%'
                        THEN 'FC'
                        ELSE 'SFC'
                    END AS zone,
                    CASE WHEN wsl.location_type = 4 THEN 'Bin'
                         WHEN wsl.location_type = 3 THEN 'Stock' END AS loc_type,
                    CONCAT(
                        CASE WHEN wsl.location_no NOT LIKE 'S%'
                              AND wsl.location_no NOT LIKE 'DC%'
                              AND wsl.location_no NOT LIKE 'K%'
                             THEN 'FC' ELSE 'SFC' END,
                        '_',
                        CASE WHEN wsl.location_type = 4 THEN 'Bin'
                             WHEN wsl.location_type = 3 THEN 'Stock' END
                    ) AS zone_type,
                    wls.storage_type,
                    wls.description,
                    wls.fill_rate,
                    COUNT(DISTINCT wsl.location_no)            AS total_locs,
                    IFNULL(SUM(wls.volume), 0)                 AS total_volume,
                    IFNULL(SUM(wls.volume * wls.fill_rate), 0) AS allowable_cap,
                    IFNULL(SUM(inv.sku_vol), 0)                AS used_volume
                FROM yamibuy_wh.wh_storage_location wsl
                LEFT JOIN yamibuy_wh.wh_location_size wls
                    ON wls.size_id = wsl.size_id
                LEFT JOIN (
                    SELECT wit.warehouse_number, wit.location_no,
                           SUM(wwg.volume * wit.quantity) AS sku_vol
                    FROM yamibuy_wh.wh_inventory_transaction wit
                    JOIN yamibuy_im.im_item ii ON ii.item_number = wit.item_number
                    JOIN yamibuy_master.xysc_wearhouse_goods wwg
                        ON wwg.goods_id = ii.goods_id
                    WHERE wit.warehouse_number = '002'
                      AND wit.quantity > 0
                    GROUP BY wit.warehouse_number, wit.location_no
                ) inv ON inv.warehouse_number = wsl.warehouse_number
                     AND inv.location_no       = wsl.location_no
                WHERE wsl.warehouse_number = '002'
                  AND wsl.location_type IN (3, 4)
                  AND wsl.size_id NOT IN (13, 33)
                GROUP BY 1, 2, 3, 4, 5, 6
            """)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()
        df_cap = pd.DataFrame(rows, columns=cols)
        for c in ["allowable_cap", "used_volume"]:
            df_cap[c] = pd.to_numeric(df_cap[c], errors="coerce").fillna(0)
        df_cap["free_volume"] = df_cap["allowable_cap"] - df_cap["used_volume"]
        return df_cap, None
    except Exception as e:
        return None, str(e)

@st.cache_data(ttl=3600)
def load():
    import glob, re
    files = sorted(glob.glob("results/segment_sku_detail_*.csv"))
    if not files:
        raise FileNotFoundError("No segment_sku_detail_*.csv found")
    latest = files[-1]
    df = pd.read_csv(latest)
    m = re.search(r"(\d{8})_(\d{4})", latest)
    ts = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:8]} {m.group(2)[:2]}:{m.group(2)[2:]}" if m else "unknown"
    return df, ts

def load_middle_locations(skus):
    """Real-time: fetch FC lot data for MIDDLE FC→DC SKUs from MySQL."""
    return _load_fc_locations(skus)

def load_tail_locations(skus):
    """Real-time: fetch FC location data for TAIL SKUs (full clear)."""
    return _load_fc_locations(skus)

def _load_fc_locations(skus):
    """Shared: fetch FC lot/location data for given SKU list."""
    import os
    from pathlib import Path
    try:
        _load_env()
        import pymysql
        conn = pymysql.connect(
            host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
            connect_timeout=8,
        )
        SQL = """
            SELECT
                li.item_number,
                li.location_no,
                CASE WHEN wsl.location_type = 4 THEN 'Bin'
                     WHEN wsl.location_type = 3 THEN 'Stock' END AS loc_type,
                SUM(li.quantity) AS qty,
                MAX(CASE WHEN ib.qc_ExpiredDate > 0
                         THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) END) AS exp_date,
                MAX(IFNULL(iie.need_minimal_exp, 0)) AS need_minimal_exp
            FROM yamibuy_wh.wh_lot_inventory li
            JOIN yamibuy_wh.wh_storage_location wsl
                ON wsl.location_no = li.location_no AND wsl.warehouse_number = li.warehouse_number
            LEFT JOIN yamibuy_wh.wh_lot_master lm ON li.lot_id = lm.lot_id
            LEFT JOIN yamibuy_wh.wh_inbound_batch ib ON lm.source_number = ib.inbound_number
            LEFT JOIN yamibuy_im.im_item_extend iie ON iie.item_number = li.item_number
            WHERE li.warehouse_number = '002'
              AND li.quantity > 0
              AND wsl.location_type IN (3, 4)
              AND wsl.size_id NOT IN (13, 33)
              AND (li.location_no NOT LIKE 'S%%'
               AND li.location_no NOT LIKE 'DC%%'
               AND li.location_no NOT LIKE 'K%%')
              AND li.item_number IN ({ph})
            GROUP BY li.item_number, li.location_no, wsl.location_type
            HAVING SUM(li.quantity) > 0
        """
        all_rows, all_cols = [], None
        chunks = [skus[i:i+500] for i in range(0, len(skus), 500)]
        with conn.cursor() as cur:
            for chunk in chunks:
                ph = ",".join(["%s"]*len(chunk))
                cur.execute(SQL.format(ph=ph), chunk)
                if all_cols is None:
                    all_cols = [d[0] for d in cur.description]
                all_rows.extend(cur.fetchall())
        conn.close()
        df = pd.DataFrame(all_rows, columns=all_cols)
        df["item_number"] = df["item_number"].astype(str)
        df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        df["exp_date"] = pd.to_datetime(df["exp_date"], errors="coerce")
        df["need_minimal_exp"] = pd.to_numeric(df["need_minimal_exp"], errors="coerce").fillna(0)
        return df, None
    except Exception as e:
        return None, str(e)

def select_middle_locations(df_lot, df_mid_skus):
    """
    Apply Sheet location selection rules per MIDDLE SKU.
    Returns DataFrame with one row per location to transfer.
    """
    results = []
    for _, sku_row in df_mid_skus.iterrows():
        sku    = sku_row["item_number"]
        excess = sku_row["excess_qty"]
        keep   = sku_row["keep_qty"]
        locs   = df_lot[df_lot["item_number"]==sku].copy()
        if locs.empty or excess <= 0:
            continue

        has_exp   = bool(locs["need_minimal_exp"].max() == 1)
        loc_count = len(locs)

        def _append(loc, xfer, rule):
            results.append({
                "item_number":     sku,
                "location_no":     loc["location_no"],
                "loc_type":        loc["loc_type"],
                "loc_qty":         loc["qty"],
                "qty_to_transfer": round(xfer),
                "exp_date":        str(loc["exp_date"])[:10] if pd.notna(loc.get("exp_date")) else "",
                "rule":            rule,
                "excess_qty":      excess,
                "keep_qty":        keep,
            })

        if has_exp and loc_count > 1:
            locs_s = locs.sort_values("exp_date", ascending=False).reset_index(drop=True)
            cum = 0
            for _, loc in locs_s.iterrows():
                xfer = min(loc["qty"], excess - cum)
                if xfer <= 0: break
                _append(loc, xfer, "3_ExpMultiLoc_FurthestFirst")
                cum += xfer
                if cum >= excess: break
        elif has_exp and loc_count == 1:
            loc = locs.iloc[0]
            xfer = min(loc["qty"], excess)
            if xfer > 0: _append(loc, xfer, "4_ExpSingleLoc_ExcessOnly")
        elif loc_count > 1:
            locs_s = locs.sort_values("qty", ascending=False).reset_index(drop=True)
            diffs  = (locs_s["qty"] - excess).abs()
            best   = locs_s.iloc[diffs.idxmin()]
            if abs(best["qty"] - excess) / max(excess, 1) <= 0.15:
                _append(best, best["qty"], "1_NoExpMultiLoc_ClosestSingle")
            else:
                locs_asc = locs.sort_values("qty", ascending=True)
                cum = 0
                for _, loc in locs_asc.iterrows():
                    if cum >= excess: break
                    _append(loc, loc["qty"], "1_NoExpMultiLoc_Accumulate")
                    cum += loc["qty"]
        else:
            loc = locs.iloc[0]
            xfer = min(loc["qty"], excess)
            if xfer > 0: _append(loc, xfer, "2_NoExpSingleLoc_ExcessOnly")

    return pd.DataFrame(results)

def select_tail_locations(df_lot_tail):
    """
    TAIL FC→DC: full clear — every FC location for the SKU.
    Returns one row per location, sorted by location_no (area-based sequential ops).
    """
    tail_fc = df_lot_tail[df_lot_tail["loc_type"].notna()].copy()
    tail_fc["qty_to_transfer"] = tail_fc["qty"]
    tail_fc["rule"] = "TAIL_FullClear"
    return tail_fc[["item_number","location_no","loc_type","qty",
                     "qty_to_transfer","exp_date","need_minimal_exp","rule"]]\
             .sort_values(["item_number","location_no"]).reset_index(drop=True)

def load_top_sfc_locations(skus):
    """Real-time: fetch SFC location data for TOP SKUs (DC→FC)."""
    import os
    from pathlib import Path
    try:
        _load_env()
        import pymysql
        conn = pymysql.connect(
            host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
            connect_timeout=8,
        )
        SQL = """
            SELECT
                wit.item_number,
                wit.location_no,
                CASE WHEN wsl.location_type = 4 THEN 'Bin'
                     WHEN wsl.location_type = 3 THEN 'Stock' END AS loc_type,
                wls.storage_type,
                wls.volume        AS loc_volume_in3,
                wls.fill_rate,
                SUM(wit.quantity) AS qty
            FROM yamibuy_wh.wh_inventory_transaction wit
            JOIN yamibuy_wh.wh_storage_location wsl
                ON wsl.location_no = wit.location_no AND wsl.warehouse_number = wit.warehouse_number
            JOIN yamibuy_wh.wh_location_size wls ON wls.size_id = wsl.size_id
            WHERE wit.warehouse_number = '002'
              AND wit.quantity > 0
              AND wsl.location_type IN (3, 4)
              AND (wit.location_no LIKE 'S%%'
                OR wit.location_no LIKE 'DC%%'
                OR wit.location_no LIKE 'K%%')
              AND wit.item_number IN ({ph})
            GROUP BY wit.item_number, wit.location_no, wsl.location_type,
                     wls.storage_type, wls.volume, wls.fill_rate
            HAVING SUM(wit.quantity) > 0
            ORDER BY wit.item_number, wit.location_no
        """
        all_rows, all_cols = [], None
        chunks = [skus[i:i+500] for i in range(0, len(skus), 500)]
        with conn.cursor() as cur:
            for chunk in chunks:
                ph = ",".join(["%s"]*len(chunk))
                cur.execute(SQL.format(ph=ph), chunk)
                if all_cols is None:
                    all_cols = [d[0] for d in cur.description]
                all_rows.extend(cur.fetchall())
        conn.close()
        df = pd.DataFrame(all_rows, columns=all_cols)
        df["item_number"] = df["item_number"].astype(str)
        df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        return df, None
    except Exception as e:
        return None, str(e)

def select_top_locations(df_sfc_locs, df_top_skus):
    """
    TOP DC→FC: pick minimum SFC locations (ascending qty) until
    fc_qty + cum_qty >= 28 × avg_daily_orders.
    Whole-location transfer. Sorted by location_no for sequential ops.
    """
    results = []
    for _, sku_row in df_top_skus.iterrows():
        sku    = sku_row["item_number"]
        fc_now = sku_row["fc_qty"]
        adl    = sku_row["avg_daily_orders"]
        target = adl * 28
        needed = max(0, target - fc_now)

        locs = df_sfc_locs[df_sfc_locs["item_number"]==sku]\
                 .sort_values("qty", ascending=True).reset_index(drop=True)
        if locs.empty:
            continue

        cum = 0
        for _, loc in locs.iterrows():
            results.append({
                "item_number":     sku,
                "location_no":     loc["location_no"],
                "loc_type":        loc["loc_type"],
                "storage_type":    loc.get("storage_type",""),
                "loc_qty":         loc["qty"],
                "qty_to_transfer": round(loc["qty"]),
                "rule":            "TOP_DC_TO_FC",
                "fc_qty_before":   fc_now,
                "target_qty":      round(target),
                "needed_qty":      round(needed),
            })
            cum += loc["qty"]
            if fc_now + cum >= target and needed > 0:
                break  # reached 28D2S target

    return pd.DataFrame(results).sort_values(["item_number","location_no"]).reset_index(drop=True)

def run_planning(df_in, p):
    """
    Build transfer schedule — fully vectorized, fast.
    """
    import numpy as np
    from datetime import date, timedelta

    df = df_in.copy()
    adl = df["avg_daily_orders"].values
    fc  = df["fc_qty"].values
    sfc = df["sfc_qty"].values
    vol = df["unit_volume_cbm"].fillna(0).values
    seg = df["seg"].values

    fc_d2s = np.where(adl > 0, fc / adl, np.nan)

    target_qty = np.where(adl > 0, np.round(adl * p["mid_target_d2s"]), 0)
    top_target = np.where(adl > 0, np.round(adl * p["top_target_d2s"]), 0)

    action       = np.full(len(df), "KEEP", dtype=object)
    transfer_qty = np.zeros(len(df))
    priority     = np.full(len(df), 999.0)

    # ── Priority: lower number = processed first ─────────────
    # Tier prefix ensures correct order regardless of within-tier score
    # FC→DC: TAIL (1xxx) > MIDDLE (2xxx)
    # DC→FC: TOP  (1xxx) > MIDDLE (2xxx)

    # ── TOP DC→FC (priority 1000-1999) ───────────────────────
    m_top = (seg == "TOP") & (adl > 0) & (np.isnan(fc_d2s) | (fc_d2s < p["top_trigger_d2s"]))
    needed_top = np.maximum(0, top_target - fc)
    xfer_top   = np.minimum(needed_top, sfc)
    active_top = m_top & (needed_top > 0) & (sfc > 0)
    action[active_top]       = "DC_TO_FC"
    transfer_qty[active_top] = np.round(xfer_top[active_top])
    d2s_top = np.where(np.isnan(fc_d2s), 0, fc_d2s)
    # P1: lowest D2S first (most urgent), normalized to 0-999
    priority[active_top] = 1000 + (d2s_top[active_top] / max(p["top_trigger_d2s"], 1)) * 999

    # ── MIDDLE DC→FC (priority 2000-2999) ────────────────────
    d2s_v = np.where(np.isnan(fc_d2s), 0, fc_d2s)
    needed_mid = np.maximum(0, target_qty - fc)
    xfer_mid   = np.minimum(needed_mid, sfc)
    m_mid_in   = (seg == "MIDDLE") & (adl > 0) & (d2s_v <= p["mid_routine_d2s"]) & (sfc > 0) & (needed_mid > 0)
    action[m_mid_in]       = "DC_TO_FC"
    transfer_qty[m_mid_in] = np.round(xfer_mid[m_mid_in])
    # P1 D2S lowest, P2 batch vol largest, P3 daily sales highest → all normalized
    max_d2s = max(p["mid_routine_d2s"], 1)
    max_vol = max(float((xfer_mid * vol).max()) if m_mid_in.any() else 1, 1)
    max_adl = max(float(adl[m_mid_in].max()) if m_mid_in.any() else 1, 1)
    priority[m_mid_in] = (
        2000
        + (d2s_v[m_mid_in] / max_d2s) * 600          # P1: D2S (0-600)
        - ((xfer_mid * vol)[m_mid_in] / max_vol) * 300 # P2: batch vol (0-300, negative)
        - (adl[m_mid_in] / max_adl) * 99               # P3: daily sales (0-99, negative)
    )

    # ── TAIL FC→DC (priority 1000-1999) ──────────────────────
    m_tail = (seg == "TAIL") & (fc > 0)
    action[m_tail]       = "FC_TO_DC"
    transfer_qty[m_tail] = fc[m_tail]
    tail_vol = transfer_qty * vol
    max_tail_vol = max(float(tail_vol[m_tail].max()) if m_tail.any() else 1, 1)
    # Largest volume first (priority = small number = first processed)
    priority[m_tail] = 1000 + (1 - tail_vol[m_tail] / max_tail_vol) * 999

    # ── MIDDLE FC→DC (priority 2000-2999) ────────────────────
    keep_qty   = np.where(adl > 0, np.round(adl * p["mid_evict_d2s"]), fc)
    excess     = fc - keep_qty
    m_mid_out  = (seg == "MIDDLE") & (adl > 0) & (d2s_v > p["mid_evict_d2s"]) & (excess > p["min_loc_qty"]) & (action == "KEEP")
    action[m_mid_out]       = "FC_TO_DC"
    transfer_qty[m_mid_out] = np.round(excess[m_mid_out])
    excess_vol = excess * vol
    max_ev = max(float(excess_vol[m_mid_out].max()) if m_mid_out.any() else 1, 1)
    max_ex = max(float(excess[m_mid_out].max()) if m_mid_out.any() else 1, 1)
    # P1 excess volume largest, P2 excess qty
    priority[m_mid_out] = (
        2000
        + (1 - excess_vol[m_mid_out] / max_ev) * 700
        + (1 - excess[m_mid_out]     / max_ex) * 299
    )

    df["action"]       = action
    df["transfer_qty"] = transfer_qty
    df["transfer_vol"] = transfer_qty * vol
    df["priority"]     = priority

    df_in_  = df[df["action"]=="DC_TO_FC"].sort_values("priority").reset_index(drop=True)
    df_out_ = df[df["action"]=="FC_TO_DC"].sort_values("priority").reset_index(drop=True)

    # ── Day-by-day schedule ───────────────────────────────────
    start = date.today()
    records = []

    idx_in  = 0
    idx_out = 0
    n_in    = len(df_in_)
    n_out   = len(df_out_)

    PALLET_VOL = 48 * 40 * 72 * 0.8  # 6ft pallet usable volume in³ = 110,592

    cum_vol_out = 0.0  # cumulative FC→DC volume released
    cum_vol_in  = 0.0  # cumulative DC→FC volume moved in

    for day in range(1, p["plan_days"] + 1):
        dt = str(start + timedelta(days=day - 1))

        # ── Group A: workers_out handle FC→DC independently ──
        # TAIL first (1000-1999), then MIDDLE FC→DC (2000-2999)
        pallet_cap_out = p.get("daily_pallet_cap_out", float("inf"))
        sku_out = 0
        pal_out = 0.0
        batch_out_idx = []
        i = idx_out
        while i < n_out and sku_out < p["daily_cap_out"]:
            row = df_out_.iloc[i]
            row_pal = np.ceil(row["transfer_vol"] / PALLET_VOL)
            if pal_out + row_pal <= pallet_cap_out:
                batch_out_idx.append(i)
                sku_out += 1
                pal_out += row_pal
                cum_vol_out += row["transfer_vol"]
            i += 1
        idx_out = i

        # ── Group B: workers_in handle DC→FC independently ───
        # TOP first (1000-1999): NO balance constraint — locations already verified sufficient
        # MIDDLE DC→FC (2000-2999): cumulative volume balance applies
        pallet_cap_in = p.get("daily_pallet_cap_in", float("inf"))
        sku_in = 0
        pal_in = 0.0
        batch_in_idx = []
        j = idx_in
        while j < n_in and sku_in < p["daily_cap_in"]:
            row = df_in_.iloc[j]
            row_pal = np.ceil(row["transfer_vol"] / PALLET_VOL)
            is_top = row["seg"] == "TOP"
            # TOP: unrestricted (location balance pre-verified: 11,958 released >> 725 needed)
            # MIDDLE: needs cumulative volume balance
            balance_ok = is_top or (cum_vol_in + row["transfer_vol"] <= cum_vol_out)
            if pal_in + row_pal <= pallet_cap_in and balance_ok:
                batch_in_idx.append(j)
                sku_in += 1
                pal_in += row_pal
                cum_vol_in += row["transfer_vol"]
            j += 1
        idx_in = j

        for act, idx_list, src_df in [
            ("FC_TO_DC", batch_out_idx, df_out_),
            ("DC_TO_FC", batch_in_idx,  df_in_),
        ]:
            if idx_list:
                tmp = src_df.iloc[idx_list][["item_number","seg","transfer_qty","transfer_vol","priority"]].copy()
                tmp["day"]     = day
                tmp["date"]    = dt
                tmp["action"]  = act
                tmp["pallets"] = np.ceil(tmp["transfer_vol"] / PALLET_VOL).astype(int)
                records.append(tmp)

        if idx_in >= n_in and idx_out >= n_out:
            break

    if not records:
        return pd.DataFrame(), pd.DataFrame(), {}

    df_sched = pd.concat(records, ignore_index=True)

    # daily summary
    daily = df_sched.groupby(["day","date","action"]).agg(
        sku_count   =("item_number",  "count"),
        transfer_qty=("transfer_qty", "sum"),
        transfer_vol=("transfer_vol", "sum"),
        pallets     =("pallets",      "sum"),
    ).reset_index()

    done_in  = int((df_sched["action"]=="DC_TO_FC").sum())
    done_out = int((df_sched["action"]=="FC_TO_DC").sum())
    vol_in   = float(df_sched[df_sched["action"]=="DC_TO_FC"]["transfer_vol"].sum())
    vol_out  = float(df_sched[df_sched["action"]=="FC_TO_DC"]["transfer_vol"].sum())
    total_p  = int(np.ceil((vol_in + vol_out) / p["pallet_vol_in3"]))

    summary = dict(
        total_in=n_in,   done_in=done_in,   pct_in=done_in/max(n_in,1)*100,
        total_out=n_out, done_out=done_out, pct_out=done_out/max(n_out,1)*100,
        vol_in=vol_in, vol_out=vol_out,
        vol_balance=vol_out - vol_in,  # positive = more released than moved in (good)
        total_pallets=total_p,
        pallets_per_day=total_p / p["plan_days"],
        rem_in=n_in - done_in, rem_out=n_out - done_out,
    )
    return df_sched, daily, summary

def calc_ideal(df_in, aod_top, ol_top, ol_tail,
               top_trigger=28, top_target=28,
               mid_routine=28, mid_emergency=14,
               mid_evict=30, mid_target=28,
               min_loc_qty=5):
    """
    动态计算理想状态 —— 基于当前 sidebar 阈值实时运算，不依赖静态 CSV。

    TOP  : FC D2S < top_trigger → DC_TO_FC 补到 top_target 天
    TAIL : FC > 0 → FC_TO_DC 全清
    MIDDLE DC_TO_FC (SFC Return Pool):
      触发：FC D2S ≤ mid_routine (28D), Emergency ≤ mid_emergency (14D)
      Required Qty = 28D2S - FC Qty
      Priority: P1 FC D2S 最低, P2 Batch Volume 最大, P3 Daily Sales 最高
    MIDDLE FC_TO_DC (Move Out Pool):
      触发：FC D2S > mid_evict (30D)
      Excess = FC Qty - 30D2S Qty
      Priority: P1 Excess Volume 最大, P2 Excess Qty 最高
    """
    import numpy as np

    rows = []
    for _, r in df_in.iterrows():
        fc    = float(r["fc_qty"])
        sfc   = float(r["sfc_qty"])
        adl   = float(r.get("avg_daily_orders", 0) or 0)
        vol   = float(r.get("unit_volume_cbi", 0) or float(r.get("unit_volume_cbm", 0) or 0))
        seg   = str(r["seg"])

        fc_d2s       = (fc / adl) if adl > 0 else float("nan")
        target_qty   = round(adl * mid_target, 0) if adl > 0 else 0.0

        action        = "KEEP"
        transfer_qty  = 0.0
        fc_ideal      = fc
        sfc_ideal     = sfc
        priority      = 999.0
        reason        = ""

        if seg == "TOP":
            if adl > 0 and (pd.isna(fc_d2s) or fc_d2s < top_trigger):
                needed = max(0.0, round(adl * top_target, 0) - fc)
                if needed > 0 and sfc > 0:
                    xfer          = min(needed, sfc)
                    action        = "DC_TO_FC"
                    transfer_qty  = round(xfer, 0)
                    fc_ideal      = fc + xfer
                    sfc_ideal     = sfc - xfer
                    d2s_val       = fc_d2s if not pd.isna(fc_d2s) else 0.0
                    reason        = f"FC D2S={d2s_val:.1f} < {top_trigger}"

        elif seg == "MIDDLE":
            if adl > 0:
                d2s = fc_d2s if not pd.isna(fc_d2s) else 0.0

                # ── DC→FC (SFC Return) ────────────────────────
                if d2s <= mid_routine and sfc > 0:
                    needed = max(0.0, target_qty - fc)
                    if needed > 0:
                        xfer          = min(needed, sfc)
                        action        = "DC_TO_FC"
                        transfer_qty  = round(xfer, 0)
                        fc_ideal      = fc + xfer
                        sfc_ideal     = sfc - xfer
                        batch_vol     = xfer * vol
                        emerg         = d2s <= mid_emergency
                        reason        = f"FC D2S={d2s:.1f} {'[EMERGENCY]' if emerg else '[ROUTINE]'}"
                        # P1: D2S 最低优先 → 用负值（越低越优先）
                        # P2: Batch Volume 最大 → 负值
                        # P3: Daily Sales 最高 → 负值
                        priority = d2s * 1e6 - batch_vol * 1e3 - adl

                # ── FC→DC (Move Out) ──────────────────────────
                elif d2s > mid_evict:
                    keep_qty  = round(adl * mid_evict, 0)
                    excess    = fc - keep_qty
                    if excess > min_loc_qty:
                        excess_vol    = excess * vol
                        action        = "FC_TO_DC"
                        transfer_qty  = round(excess, 0)
                        fc_ideal      = fc - excess
                        sfc_ideal     = sfc + excess
                        reason        = f"FC D2S={d2s:.1f} > {mid_evict}, excess={excess:.0f}"
                        # P1: Excess Volume 最大 → 负值
                        # P2: Excess Qty 最高 → 负值
                        priority = -excess_vol * 1e3 - excess

        elif seg == "TAIL":
            if fc > 0:
                action        = "FC_TO_DC"
                transfer_qty  = fc
                fc_ideal      = 0.0
                sfc_ideal     = sfc + fc
                reason        = "TAIL: clear FC"
                priority      = 100.0

        rows.append({
            "item_number":  str(r["item_number"]),
            "seg":          seg,
            "biz_type":     str(r.get("biz_type", "")),
            "fc_qty":       fc,
            "sfc_qty":      sfc,
            "fc_d2s":       round(fc_d2s, 1) if not pd.isna(fc_d2s) else None,
            "avg_daily":    round(adl, 2),
            "unit_vol":     round(vol, 4),
            "action":       action,
            "transfer_qty": transfer_qty,
            "fc_ideal":     fc_ideal,
            "sfc_ideal":    sfc_ideal,
            "fc_vol_now":   round(fc   * vol, 0),
            "sfc_vol_now":  round(sfc  * vol, 0),
            "fc_vol_ideal": round(fc_ideal  * vol, 0),
            "sfc_vol_ideal":round(sfc_ideal * vol, 0),
            "priority":     round(priority, 3),
            "reason":       reason,
        })
    return pd.DataFrame(rows)

df, DATA_TS = load()

# ── 实时库存覆盖 CSV qty（MySQL 实时）────────────────────────
df_inv_rt, inv_err = load_inventory()
if df_inv_rt is not None:
    df["item_number"] = df["item_number"].astype(str)
    df_inv_rt["item_number"] = df_inv_rt["item_number"].astype(str)
    df = df.merge(df_inv_rt, on="item_number", how="left", suffixes=("_csv",""))
    df["fc_qty"]  = df["fc_qty"].fillna(df["fc_qty_csv"]).fillna(0)
    df["sfc_qty"] = df["sfc_qty"].fillna(df["sfc_qty_csv"]).fillna(0)
    df.drop(columns=["fc_qty_csv","sfc_qty_csv"], errors="ignore", inplace=True)
    INV_TS = "live"
else:
    INV_TS = f"CSV only ({DATA_TS})"  # fallback to CSV if MySQL unavailable

# 分母在过滤后重算，这里先占位
N   = 0
TOL = 0
TU  = 0
TC  = 0

# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🎛 Tier Thresholds")
    st.markdown("---")

    # ── Session state init ──────────────────────────────────
    if "aod_top_val" not in st.session_state: st.session_state.aod_top_val = 21
    if "ol_top_val"  not in st.session_state: st.session_state.ol_top_val  = 30
    if "ol_tail_val" not in st.session_state: st.session_state.ol_tail_val = 8

    def sync_from_slider(val_key, sld_key):
        st.session_state[val_key] = st.session_state[sld_key]
    def sync_from_num(val_key, num_key):
        st.session_state[val_key] = st.session_state[num_key]

    # ── AOD ─────────────────────────────────────────────────
    st.markdown("**TOP** — FC Primary")
    st.slider("Active Order Days ≥", 1, 30, key="aod_top_sld",
              value=st.session_state.aod_top_val,
              on_change=sync_from_slider, args=("aod_top_val", "aod_top_sld"))
    st.number_input("", 1, 30, key="aod_top_num",
                    value=st.session_state.aod_top_val, step=1,
                    label_visibility="collapsed",
                    on_change=sync_from_num, args=("aod_top_val", "aod_top_num"))
    aod_top = st.session_state.aod_top_val

    # ── OL TOP ───────────────────────────────────────────────
    st.slider("30D Order Lines ≥", 1, 300, key="ol_top_sld",
              value=st.session_state.ol_top_val, step=1,
              on_change=sync_from_slider, args=("ol_top_val", "ol_top_sld"))
    st.number_input(" ", 1, 500, key="ol_top_num",
                    value=st.session_state.ol_top_val, step=1,
                    label_visibility="collapsed",
                    on_change=sync_from_num, args=("ol_top_val", "ol_top_num"))
    ol_top = st.session_state.ol_top_val

    st.markdown("---")
    st.markdown("**TAIL** — DC Primary")

    # ── OL TAIL ──────────────────────────────────────────────
    st.slider("30D Order Lines <", 1, 50, key="ol_tail_sld",
              value=st.session_state.ol_tail_val,
              on_change=sync_from_slider, args=("ol_tail_val", "ol_tail_sld"))
    st.number_input("  ", 1, 50, key="ol_tail_num",
                    value=st.session_state.ol_tail_val, step=1,
                    label_visibility="collapsed",
                    on_change=sync_from_num, args=("ol_tail_val", "ol_tail_num"))
    ol_tail = st.session_state.ol_tail_val

    st.markdown("---")
    st.markdown("**Targets**")
    t_sku   = st.number_input("Target SKU %",   value=10.2, step=0.1, format="%.1f")
    t_ol    = st.number_input("Target OL %",    value=70.2, step=0.1, format="%.1f")
    t_units = st.number_input("Target Units %", value=77.7, step=0.1, format="%.1f")
    t_cbi   = st.number_input("Target CBI %",   value=80.4, step=0.1, format="%.1f")

    TARGETS = {"SKU %": t_sku, "OL %": t_ol, "Units %": t_units, "CBI %": t_cbi}

    st.markdown("---")
    st.caption(f"Rolling 30 inv-days · wh=002 · 1P+FBY · {DATA_TS}")

# ── Classify ─────────────────────────────────────────────────
def classify(r):
    if r.active_order_days >= aod_top and r.order_lines_30d >= ol_top:
        return "TOP"
    if r.order_lines_30d < ol_tail:
        return "TAIL"
    return "MIDDLE"

df["seg"]      = df.apply(classify, axis=1)
df["biz_type"] = df["item_number"].astype(str).str[0].map({"1": "自营", "5": "FBY"}).fillna("Other")

# ── 只保留库内有库存的 SKU（fc_qty>0 或 sfc_qty>0）────────────
for c in ["fc_qty", "sfc_qty"]:
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
n_all_skus = len(df)
df = df[(df["fc_qty"] > 0) | (df["sfc_qty"] > 0)].copy()

# 重新算分母（基于有库存 SKU）
N   = len(df)
TOL = df["order_lines_30d"].sum()
TU  = df["sales_units_30d"].sum()
TC  = df["daily_sales_cbi"].sum() if "daily_sales_cbi" in df.columns else df["daily_sales_cbm"].sum()

def stats(seg):
    s = df[df["seg"] == seg]
    return dict(
        n=len(s),
        psku=len(s)/N*100,
        pol=s["order_lines_30d"].sum()/TOL*100 if TOL else 0,
        pu=s["sales_units_30d"].sum()/TU*100 if TU else 0,
        pc=s["daily_sales_cbm"].sum()/TC*100 if TC else 0,
        fc=int(s["fc_qty"].sum()),
        sfc=int(s["sfc_qty"].sum()),
    )

T = stats("TOP"); M = stats("MIDDLE"); TL = stats("TAIL")

# ── Header ────────────────────────────────────────────────────
st.markdown("## NJ Segment Simulator")
st.markdown(f"<span style='color:#999;font-size:.9rem'>"
            f"In-stock SKUs: {N:,} &nbsp;|&nbsp; "
            f"Sales data: {DATA_TS} &nbsp;|&nbsp; "
            f"Inventory: {'🟢 Live' if INV_TS == 'live' else '🟡 ' + INV_TS}"
            f"</span>", unsafe_allow_html=True)

# ── Top-level page tabs ───────────────────────────────────────
page_overview, page_zone, page_compare, page_capacity, page_planning = st.tabs([
    "📊 Overview",
    "🗺 Zone View (FC / SFC)",
    "⚖️ Before vs After",
    "📦 Capacity",
    "📋 Planning",
])

with page_overview:

    # ── Tier Cards ────────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)

    def card(col, label, st_data, color):
        with col:
            st.markdown(f"""
            <div class="seg-card" style="background:{color}">
              <h2>{label}</h2>
              <p class="big">{st_data['n']}</p>
              <p class="sub">{st_data['psku']:.1f}% of total SKUs</p>
              <div class="row">
                <div class="stat">
                  <div class="stat-label">Order Lines</div>
                  <div class="stat-val">{st_data['pol']:.1f}%</div>
                </div>
                <div class="stat">
                  <div class="stat-label">Sales Units</div>
                  <div class="stat-val">{st_data['pu']:.1f}%</div>
                </div>
                <div class="stat">
                  <div class="stat-label">Daily in³</div>
                  <div class="stat-val">{st_data['pc']:.1f}%</div>
                </div>
                <div class="stat">
                  <div class="stat-label">FC Qty</div>
                  <div class="stat-val">{st_data['fc']:,}</div>
                </div>
              </div>
            </div>
            """, unsafe_allow_html=True)

    card(c1, "TOP — FC Primary",   T,  COLORS["TOP"])
    card(c2, "MIDDLE — Dual WH",   M,  COLORS["MIDDLE"])
    card(c3, "TAIL — DC Primary",  TL, COLORS["TAIL"])

    st.divider()

    # ── Two column layout ─────────────────────────────────────────
    left, right = st.columns([1, 1], gap="large")

# LEFT: Donut charts
    with left:
        st.markdown("#### SKU & OL Distribution")

        seg_df = pd.DataFrame([
            {"Tier": "TOP",    "SKUs": T["n"],  "OL": T["pol"]},
            {"Tier": "MIDDLE", "SKUs": M["n"],  "OL": M["pol"]},
            {"Tier": "TAIL",   "SKUs": TL["n"], "OL": TL["pol"]},
        ])

        fc1, fc2 = st.columns(2)
        with fc1:
            fig_s = go.Figure(go.Pie(
                labels=seg_df["Tier"], values=seg_df["SKUs"],
                hole=.55, marker_colors=[COLORS[t] for t in seg_df["Tier"]],
                textinfo="percent", showlegend=False,
            ))
            fig_s.update_layout(margin=dict(t=30,b=0,l=0,r=0), height=200,
                                title=dict(text="SKUs", x=.5, font_size=13))
            st.plotly_chart(fig_s, use_container_width=True)

        with fc2:
            fig_o = go.Figure(go.Pie(
                labels=seg_df["Tier"],
                values=[T["pol"], M["pol"], TL["pol"]],
                hole=.55, marker_colors=[COLORS[t] for t in seg_df["Tier"]],
                textinfo="percent", showlegend=False,
            ))
            fig_o.update_layout(margin=dict(t=30,b=0,l=0,r=0), height=200,
                                title=dict(text="Order Lines", x=.5, font_size=13))
            st.plotly_chart(fig_o, use_container_width=True)

        # Legend
        st.markdown(
            " &nbsp; ".join([
                f"<span style='color:{COLORS[t]};font-size:.85rem'>■ {t}</span>"
                for t in ["TOP","MIDDLE","TAIL"]
            ]),
            unsafe_allow_html=True
        )

    # RIGHT: Target comparison
    with right:
        st.markdown("#### TOP Tier vs Target")

        actuals = {
            "SKU %":   T["psku"],
            "OL %":    T["pol"],
            "Units %": T["pu"],
            "CBI %":   T["pc"],
        }

        for metric, target in TARGETS.items():
            actual = actuals[metric]
            pct    = min(actual / target, 1.0)
            gap    = actual - target
            color  = "#5B8DEF" if gap >= 0 else "#EF5B5B"
            gap_bg = "#e8f0fe" if gap >= 0 else "#fde8e8"
            gap_fg = "#1a56db" if gap >= 0 else "#c81e1e"
            bar_w  = f"{pct*100:.0f}%"

            st.markdown(f"""
            <div class="target-row">
              <div class="target-label">{metric}</div>
              <div style="flex:1">
                <div class="bar-wrap">
                  <div class="bar-fill" style="width:{bar_w};background:{color}"></div>
                </div>
                <div style="font-size:.7rem;color:#999;margin-top:2px">
                  {actual:.1f}% &nbsp;/&nbsp; target {target:.1f}%
                </div>
              </div>
              <span class="gap-badge" style="background:{gap_bg};color:{gap_fg}">
                {gap:+.1f}%
              </span>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # FC vs SFC stacked bar
        inv_df = pd.DataFrame([
            {"Tier": "TOP",    "FC": T["fc"],   "SFC": T["sfc"]},
            {"Tier": "MIDDLE", "FC": M["fc"],   "SFC": M["sfc"]},
            {"Tier": "TAIL",   "FC": TL["fc"],  "SFC": TL["sfc"]},
        ])
        fig_inv = go.Figure()
        fig_inv.add_trace(go.Bar(name="FC",  x=inv_df["Tier"], y=inv_df["FC"],
                                 marker_color=[COLORS[t] for t in inv_df["Tier"]],
                                 opacity=.9))
        fig_inv.add_trace(go.Bar(name="SFC", x=inv_df["Tier"], y=inv_df["SFC"],
                                 marker_color=[COLORS[t] for t in inv_df["Tier"]],
                                 opacity=.45))
        fig_inv.update_layout(
            barmode="stack", height=220, margin=dict(t=30,b=0,l=0,r=0),
            title=dict(text="FC vs SFC Inventory", x=0, font_size=13),
            legend=dict(orientation="h", x=1, xanchor="right", y=1.1),
            yaxis=dict(tickformat=","), plot_bgcolor="white",
            xaxis=dict(showgrid=False), yaxis_gridcolor="#f0f0f0",
        )
        st.plotly_chart(fig_inv, use_container_width=True)

# ── Zone View Tab ─────────────────────────────────────────────
with page_zone:

    @st.cache_data
    def load_zone():
        zdf = pd.read_csv("results/zone_inventory.csv")
        zdf["qty"]         = pd.to_numeric(zdf["qty"],          errors="coerce").fillna(0)
        zdf["unit_volume_cbi"] = pd.to_numeric(zdf["unit_volume_cbm"], errors="coerce").fillna(0)
        zdf["total_volume"]= zdf["qty"] * zdf["unit_volume_cbi"]
        return zdf

    zdf = load_zone()

    # join segment info
    seg_map = df[["item_number","seg","biz_type"]].drop_duplicates("item_number")
    seg_map["item_number"] = seg_map["item_number"].astype(str)
    zdf["item_number"] = zdf["item_number"].astype(str)
    zdf = zdf.merge(seg_map, on="item_number", how="left")
    zdf["seg"]      = zdf["seg"].fillna("UNMATCHED")
    zdf["biz_type"] = zdf["biz_type"].fillna("Other")

    # ── Summary cards: FC vs SFC ──────────────────────────────
    fc_df  = zdf[zdf["zone"] == "FC"]
    sfc_df = zdf[zdf["zone"] == "SFC"]

    fc_skus  = fc_df["item_number"].nunique()
    sfc_skus = sfc_df["item_number"].nunique()
    overlap  = len(set(fc_df["item_number"]) & set(sfc_df["item_number"]))

    zc1, zc2, zc3 = st.columns(3)
    zc1.metric("FC (Zone 1) SKUs",  f"{fc_skus}",  f"{fc_df['qty'].sum():,.0f} units")
    zc2.metric("SFC (Zone 2) SKUs", f"{sfc_skus}", f"{sfc_df['qty'].sum():,.0f} units")
    zc3.metric("Overlap (both zones)", f"{overlap} SKUs",
               f"{overlap/max(fc_skus,1)*100:.1f}% of FC SKUs")

    st.divider()

    # ── Tier distribution in FC vs SFC ───────────────────────
    st.markdown("#### Tier Distribution by Zone")

    def zone_seg_summary(zone_df, zone_name):
        grp = zone_df.groupby("seg").agg(
            sku_count  =("item_number", "nunique"),
            total_qty  =("qty", "sum"),
            total_vol  =("total_volume", "sum"),
        ).reset_index()
        grp["zone"] = zone_name
        return grp

    summary_fc  = zone_seg_summary(fc_df,  "FC")
    summary_sfc = zone_seg_summary(sfc_df, "SFC")
    z_summary   = pd.concat([summary_fc, summary_sfc]).sort_values(["zone","seg"])

    za, zb = st.columns(2)
    with za:
        fig_zsku = px.bar(
            z_summary, x="seg", y="sku_count", color="zone", barmode="group",
            color_discrete_map={"FC": "#5B8DEF", "SFC": "#34C4A4"},
            title="SKU Count by Tier", labels={"sku_count": "SKUs", "seg": "Tier"},
        )
        fig_zsku.update_layout(height=280, margin=dict(t=35,b=0,l=0,r=0),
                               legend=dict(orientation="h", x=1, xanchor="right", y=1.1))
        st.plotly_chart(fig_zsku, use_container_width=True)

    with zb:
        fig_zvol = px.bar(
            z_summary, x="seg", y="total_vol", color="zone", barmode="group",
            color_discrete_map={"FC": "#5B8DEF", "SFC": "#34C4A4"},
            title="Total Volume (in³) by Tier", labels={"total_vol": "in³", "seg": "Tier"},
        )
        fig_zvol.update_layout(height=280, margin=dict(t=35,b=0,l=0,r=0),
                                legend=dict(orientation="h", x=1, xanchor="right", y=1.1))
        st.plotly_chart(fig_zvol, use_container_width=True)

    st.markdown("**Summary table**")
    pivot = z_summary.pivot_table(
        index="seg", columns="zone",
        values=["sku_count","total_qty","total_vol"],
        aggfunc="sum", fill_value=0
    ).round(0).astype(int)
    pivot.columns = [f"{v} ({z})" for v, z in pivot.columns]
    st.dataframe(pivot.reset_index().rename(columns={"seg":"Tier"}),
                 use_container_width=True, hide_index=True)

    st.divider()

    # ── Per-SKU detail ────────────────────────────────────────
    st.markdown("#### SKU-level Zone Detail")

    # aggregate to SKU level
    sku_zone = zdf.groupby(["item_number","zone","seg","biz_type"]).agg(
        locations =("location_no", "nunique"),
        qty       =("qty",         "sum"),
        volume_cbi=("total_volume","sum"),
    ).reset_index()

    # pivot to wide: fc_qty / sfc_qty side by side
    fc_s  = sku_zone[sku_zone["zone"]=="FC" ][["item_number","seg","biz_type","locations","qty","volume_cbi"]]\
              .rename(columns={"locations":"fc_locs","qty":"fc_qty","volume_cbi":"fc_vol"})
    sfc_s = sku_zone[sku_zone["zone"]=="SFC"][["item_number","locations","qty","volume_cbi"]]\
              .rename(columns={"locations":"sfc_locs","qty":"sfc_qty","volume_cbi":"sfc_vol"})

    sku_wide = fc_s.merge(sfc_s, on="item_number", how="outer")
    for c in ["fc_locs","fc_qty","fc_vol","sfc_locs","sfc_qty","sfc_vol"]:
        sku_wide[c] = sku_wide[c].fillna(0)
    for c in ["seg","biz_type"]:
        sku_wide[c] = sku_wide[c].fillna("UNMATCHED")

    sku_wide["in_both"] = (sku_wide["fc_qty"] > 0) & (sku_wide["sfc_qty"] > 0)
    sku_wide["item_number"] = sku_wide["item_number"].astype(str)

    # filters
    zf1, zf2, zf3 = st.columns([1,1,1])
    zf_zone = zf1.selectbox("Zone filter", ["ALL","FC only","SFC only","Both"],
                             key="zf_zone", label_visibility="collapsed")
    zf_seg  = zf2.selectbox("Tier", ["ALL","TOP","MIDDLE","TAIL"],
                             key="zf_seg",  label_visibility="collapsed")
    zf_biz  = zf3.selectbox("Biz", ["ALL","自营","FBY"],
                             key="zf_biz",  label_visibility="collapsed")

    zshow = sku_wide.copy()
    if zf_zone == "FC only":   zshow = zshow[(zshow["fc_qty"]>0)  & (zshow["sfc_qty"]==0)]
    if zf_zone == "SFC only":  zshow = zshow[(zshow["sfc_qty"]>0) & (zshow["fc_qty"]==0)]
    if zf_zone == "Both":      zshow = zshow[zshow["in_both"]]
    if zf_seg  != "ALL":       zshow = zshow[zshow["seg"]==zf_seg]
    if zf_biz  != "ALL":       zshow = zshow[zshow["biz_type"]==zf_biz]

    zshow = zshow.sort_values("fc_qty", ascending=False)

    st.caption(f"{len(zshow)} SKUs")
    st.dataframe(
        zshow[["item_number","seg","biz_type",
               "fc_locs","fc_qty","fc_vol",
               "sfc_locs","sfc_qty","sfc_vol","in_both"]].rename(columns={
            "item_number": "SKU",
            "seg":         "Tier",
            "biz_type":    "Biz",
            "fc_locs":     "FC Locs",
            "fc_qty":      "FC Qty",
            "fc_vol":      "FC Vol(in³)",
            "sfc_locs":    "SFC Locs",
            "sfc_qty":     "SFC Qty",
            "sfc_vol":     "SFC Vol(in³)",
            "in_both":     "Both Zones",
        }),
        use_container_width=True, hide_index=True, height=450,
        column_config={
            "SKU":        st.column_config.TextColumn(width="medium"),
            "Tier":       st.column_config.TextColumn(width="small"),
            "Biz":        st.column_config.TextColumn(width="small"),
            "FC Qty":     st.column_config.NumberColumn(format="%d"),
            "FC Vol(in³)":st.column_config.NumberColumn(format="%.1f"),
            "SFC Qty":    st.column_config.NumberColumn(format="%d"),
            "SFC Vol(in³)":st.column_config.NumberColumn(format="%.1f"),
            "Both Zones": st.column_config.CheckboxColumn(),
        }
    )

    zcsv = zshow.to_csv(index=False)
    st.download_button("⬇ Download Zone CSV", zcsv,
                       f"nj_zone_{zf_zone}_{zf_seg}.csv", "text/csv")

# ── Before vs After Tab ───────────────────────────────────────
with page_compare:
    # ── 动态计算理想状态（实时 sidebar 阈值，无 cap）─────────
    df_ideal = calc_ideal(
        df, aod_top, ol_top, ol_tail,
        top_trigger=28, top_target=28,
        mid_routine=28, mid_emergency=14,
        mid_evict=30, mid_target=28,
    )
    # df 已经是有库存全量，calc_ideal 输出行数与 Overview 完全一致

    # ── CSS helpers ───────────────────────────────────────
    def dc(val): return "#27ae60" if val>0 else "#e74c3c" if val<0 else "#999"
    def arrow(val): return "▲" if val>0 else "▼" if val<0 else "─"

    # ── Page header ───────────────────────────────────────
    st.markdown("### ⚖️ Before vs After")
    st.caption(f"TOP: AOD≥{aod_top} & OL≥{ol_top}  ·  TAIL: OL<{ol_tail}  ·  "
               f"{len(df_ideal):,} in-stock SKUs  ·  Dynamic ideal state (no daily cap)")
    st.divider()

    # ── Section 1: Overall Inventory Shift ────────────────
    st.markdown("#### Overall Inventory Shift")

    fc_now    = int(df_ideal["fc_qty"].sum())
    fc_after  = int(df_ideal["fc_ideal"].sum())
    sfc_now   = int(df_ideal["sfc_qty"].sum())
    sfc_after = int(df_ideal["sfc_ideal"].sum())

    fc_sku_b  = int((df_ideal["fc_qty"]>0).sum())
    fc_sku_a  = int((df_ideal["fc_ideal"]>0).sum())
    sfc_sku_b = int((df_ideal["sfc_qty"]>0).sum())
    sfc_sku_a = int((df_ideal["sfc_ideal"]>0).sum())

    fc_vol_b  = df_ideal["fc_vol_now"].sum()
    fc_vol_a  = df_ideal["fc_vol_ideal"].sum()
    sfc_vol_b = df_ideal["sfc_vol_now"].sum()
    sfc_vol_a = df_ideal["sfc_vol_ideal"].sum()

    def _fmt_num(v):
        if abs(v) >= 1_000_000: return f"{v/1_000_000:.1f}M"
        if abs(v) >= 1_000:     return f"{v/1_000:.0f}K"
        return f"{int(v):,}"

    def _fmt_sku(v):   return f"{int(v):,}"
    def _fmt_qty(v):   return f"{int(v):,}"
    def _fmt_vol(v):
        if abs(v) >= 1_000_000: return f"{v/1_000_000:.1f}M"
        if abs(v) >= 1_000:     return f"{v/1_000:.0f}K"
        return f"{v:.0f}"

    st.markdown("""
    <style>
    @keyframes flow { to { stroke-dashoffset: -24; } }
    .ois { border-radius:16px; border:1.5px solid #e8eaf0; padding:32px 40px;
           background:#fff; box-shadow:0 2px 12px rgba(0,0,0,.05); }
    .ois-hdr { display:flex; margin-bottom:8px; padding:0 0 12px 0;
               border-bottom:2px solid #f0f0f0; }
    .ois-hcell { flex:1; text-align:center; font-size:1.1rem; font-weight:700; color:#444; }
    .ois-hcell.mid { flex:none; width:160px; }
    .ois-row { display:flex; align-items:center; padding:20px 0;
               border-bottom:1px solid #f4f4f4; }
    .ois-row:last-child { border-bottom:none; }
    .ois-dim { width:90px; font-size:.85rem; font-weight:700; color:#bbb;
               text-transform:uppercase; letter-spacing:.07em; }
    .ois-side { flex:1; display:flex; flex-direction:column;
                align-items:center; gap:6px; }
    .ois-b { font-size:1rem; font-weight:400; color:#bbb; line-height:1; }
    .ois-a { font-size:2rem; font-weight:800; color:#222; line-height:1.1; }
    .ois-mid { width:160px; display:flex; flex-direction:column;
               align-items:center; gap:8px; }
    .ois-badge { font-size:1rem; font-weight:700; background:#eef2ff;
                 color:#3d52d5; border-radius:99px; padding:5px 16px; }
    </style>
    """, unsafe_allow_html=True)

    def _svg_arrow():
        return (
            "<svg width='120' height='20' viewBox='0 0 120 20' xmlns='http://www.w3.org/2000/svg'>"
            "<defs><marker id='ah2' markerWidth='8' markerHeight='8' refX='4' refY='4' orient='auto'>"
            "<path d='M0,0 L8,4 L0,8 Z' fill='#5B8DEF'/></marker></defs>"
            "<line x1='4' y1='10' x2='108' y2='10' stroke='#5B8DEF' stroke-width='3' "
            "stroke-dasharray='9,5' marker-end='url(#ah2)' "
            "style='animation:flow 1s linear infinite'/>"
            "</svg>"
        )

    ois_rows = [
        ("SKUs",   fc_sku_b, fc_sku_a, sfc_sku_b, sfc_sku_a, _fmt_sku),
        ("Qty",    fc_now,   fc_after, sfc_now,   sfc_after,  _fmt_qty),
        ("Volume", fc_vol_b, fc_vol_a, sfc_vol_b, sfc_vol_a,  _fmt_vol),
    ]

    hdr = (
        "<div class='ois-hdr'>"
        "  <div class='ois-dim'></div>"
        "  <div class='ois-hcell'>🏭 FC</div>"
        "  <div class='ois-hcell mid'></div>"
        "  <div class='ois-hcell'>🏬 SFC</div>"
        "</div>"
    )

    body = ""
    for dim, fb, fa, sb, sa, fmt in ois_rows:
        fd = fa - fb;  sd = sa - sb
        xfer = abs(fd)
        fc_clr  = "#e74c3c" if fd < 0 else "#27ae60" if fd > 0 else "#aaa"
        sfc_clr = "#27ae60" if sd > 0 else "#e74c3c" if sd < 0 else "#aaa"
        fc_arr  = "▼" if fd < 0 else "▲" if fd > 0 else "─"
        sfc_arr = "▲" if sd > 0 else "▼" if sd < 0 else "─"
        body += (
            f"<div class='ois-row'>"
            f"  <div class='ois-dim'>{dim}</div>"
            f"  <div class='ois-side'>"
            f"    <div class='ois-b'>{fmt(fb)}</div>"
            f"    <div class='ois-a' style='color:{fc_clr}'>{fmt(fa)}</div>"
            f"  </div>"
            f"  <div class='ois-mid'>"
            f"    {_svg_arrow()}"
            f"    <div class='ois-badge'>{fmt(xfer)}</div>"
            f"  </div>"
            f"  <div class='ois-side'>"
            f"    <div class='ois-b'>{fmt(sb)}</div>"
            f"    <div class='ois-a' style='color:{sfc_clr}'>{fmt(sa)}</div>"
            f"  </div>"
            f"</div>"
        )

    st.markdown(f"<div class='ois'>{hdr}{body}</div>", unsafe_allow_html=True)

    # ── FC 容量验证（点击展开才加载）────────────────────────
    with st.expander("🔍 FC Capacity Check (click to load)", expanded=False):
        df_cap, cap_err = load_capacity()
        if df_cap is not None:
            fc_cap  = df_cap[df_cap["zone"]=="FC"]["allowable_cap"].sum()
            fc_used = df_cap[df_cap["zone"]=="FC"]["used_volume"].sum()
            fc_free = fc_cap - fc_used
            fc_inbound_vol = max(0, fc_vol_a - fc_vol_b)
            fc_free_after  = fc_free - fc_inbound_vol
            util_before    = fc_used / max(fc_cap, 1) * 100
            util_after     = (fc_used + fc_inbound_vol) / max(fc_cap, 1) * 100
            feasible       = fc_free_after >= 0
            status_color = "#27ae60" if feasible else "#e74c3c"
            status_icon  = "✅" if feasible else "⚠️"
            status_text  = "Feasible" if feasible else "Capacity Risk"
            def _fv(v): return f"{v/1_000_000:.1f}M in³" if abs(v)>=1e6 else f"{v/1_000:.0f}K in³"
            st.markdown(f"""
            <div style="border:1.5px solid {status_color}33;border-radius:12px;
                 padding:16px 24px;background:#fafafa">
              <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
                <span style="font-size:1.1rem;font-weight:700;color:#333">
                  {status_icon} {status_text}</span>
              </div>
              <div style="display:flex;gap:32px;flex-wrap:wrap">
                <div><div style="font-size:.72rem;color:#aaa">FC Allowable Cap</div>
                     <div style="font-size:1.2rem;font-weight:700">{_fv(fc_cap)}</div></div>
                <div><div style="font-size:.72rem;color:#aaa">Currently Used</div>
                     <div style="font-size:1.2rem;font-weight:700">{_fv(fc_used)} ({util_before:.1f}%)</div></div>
                <div><div style="font-size:.72rem;color:#aaa">Need to Move In</div>
                     <div style="font-size:1.2rem;font-weight:700;color:#3d6fbe">{_fv(fc_inbound_vol)}</div></div>
                <div><div style="font-size:.72rem;color:#aaa">Free After Transfer</div>
                     <div style="font-size:1.2rem;font-weight:700;color:{status_color}">{_fv(fc_free_after)} ({util_after:.1f}%)</div></div>
              </div>
            </div>""", unsafe_allow_html=True)
        elif cap_err:
            st.caption(f"⚠️ {cap_err[:80]}")

    st.divider()

    # ── Section 2: By Segment — full cards ───────────────
    st.markdown("#### By Segment")

    # compute loc cols here so cards can use them
    dists = df_ideal.copy()

    LOCS      = ["FC", "SFC", "Overlap"]
    _LOC_HEX   = {"FC":"#5B8DEF","SFC":"#34C4A4","Overlap":"#F0A500"}
    _LOC_ICONS = {"FC":"🏭","SFC":"🏬","Overlap":"🔄"}

    def dist_table(sub):
        def _cnt(s, fc_col, sfc_col):
            return (int((s[fc_col]>0).sum()),
                    int((s[sfc_col]>0).sum()),
                    int(((s[fc_col]>0)&(s[sfc_col]>0)).sum()))
        b_fc, b_sfc, b_ov = _cnt(sub, "fc_qty",   "sfc_qty")
        a_fc, a_sfc, a_ov = _cnt(sub, "fc_ideal", "sfc_ideal")
        return [
            {"loc":"FC",      "before":b_fc,  "after":a_fc,  "delta":a_fc-b_fc},
            {"loc":"SFC",     "before":b_sfc, "after":a_sfc, "delta":a_sfc-b_sfc},
            {"loc":"Overlap", "before":b_ov,  "after":a_ov,  "delta":a_ov-b_ov},
        ]

    def loc_row(loc, d_rows):
        r = d_rows.get(loc, {"before":0,"after":0,"delta":0})
        b, a, delta = r["before"], r["after"], r["delta"]
        dclr = "#27ae60" if delta>0 else "#e74c3c" if delta<0 else "#aaa"
        arr  = "▲" if delta>0 else "▼" if delta<0 else "─"
        icon = _LOC_ICONS.get(loc, "")
        return (
            f"<div style='display:flex;justify-content:space-between;"
            f"align-items:center;padding:7px 0;border-bottom:1px solid #f0f0f0'>"
            f"<span style='font-size:.88rem;color:#444;min-width:90px'>{icon} {loc}</span>"
            f"<span style='font-size:.88rem;color:#888;min-width:60px;text-align:right'>{b:,}</span>"
            f"<span style='color:#ccc;font-size:.82rem;padding:0 6px'>→</span>"
            f"<span style='font-size:.88rem;color:#222;font-weight:700;min-width:60px'>{a:,}</span>"
            f"<span style='font-size:.85rem;font-weight:700;color:{dclr};min-width:52px;text-align:right'>"
            f"{arr} {abs(delta):,}</span>"
            f"</div>"
        )

    sc1, sc2, sc3 = st.columns(3)
    for col, seg, color in [
        (sc1, "TOP",    COLORS["TOP"]),
        (sc2, "MIDDLE", COLORS["MIDDLE"]),
        (sc3, "TAIL",   COLORS["TAIL"]),
    ]:
        sub   = df_ideal[df_ideal["seg"] == seg]
        sub_d = sub
        if sub.empty: continue

        n     = len(sub)
        fc_b  = int(sub["fc_qty"].sum());   fc_a  = int(sub["fc_ideal"].sum())
        sfc_b = int(sub["sfc_qty"].sum());  sfc_a = int(sub["sfc_ideal"].sum())
        fd    = fc_a - fc_b;  sd = sfc_a - sfc_b
        d_rows = {r["loc"]: r for r in dist_table(sub_d)}

        # volume (in³)
        fc_vb  = sub["fc_vol_now"].sum();   fc_va  = sub["fc_vol_ideal"].sum()
        sfc_vb = sub["sfc_vol_now"].sum();  sfc_va = sub["sfc_vol_ideal"].sum()
        fvd    = fc_va  - fc_vb
        svd    = sfc_va - sfc_vb

        qty_dclr_fc  = "#27ae60" if fd>0 else "#e74c3c" if fd<0 else "#aaa"
        qty_dclr_sfc = "#27ae60" if sd>0 else "#e74c3c" if sd<0 else "#aaa"
        qty_arr_fc   = "▲" if fd>0 else "▼" if fd<0 else "─"
        qty_arr_sfc  = "▲" if sd>0 else "▼" if sd<0 else "─"
        vol_dclr_fc  = "#27ae60" if fvd>0 else "#e74c3c" if fvd<0 else "#aaa"
        vol_dclr_sfc = "#27ae60" if svd>0 else "#e74c3c" if svd<0 else "#aaa"
        vol_arr_fc   = "▲" if fvd>0 else "▼" if fvd<0 else "─"
        vol_arr_sfc  = "▲" if svd>0 else "▼" if svd<0 else "─"

        col.markdown(f"""
        <div style="border:2px solid {color};border-radius:12px;padding:16px 18px">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px">
            <span style="background:{color};color:#fff;border-radius:6px;
                  padding:4px 14px;font-size:1rem;font-weight:700">{seg}</span>
            <span style="color:#777;font-size:.95rem">{n:,} SKUs total</span>
          </div>
          <div style="font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;
               color:#bbb;margin-bottom:8px">SKU Location (Before → After)</div>
          {loc_row("FC",      d_rows)}
          {loc_row("SFC",     d_rows)}
          {loc_row("Overlap", d_rows)}
          <div style="margin-top:14px;padding-top:12px;border-top:2px solid #f4f4f4">
            <div style="font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;
                 color:#bbb;margin-bottom:8px">Inventory Qty (Before → After)</div>
            <div style="display:flex;gap:16px">
              <div style="flex:1">
                <div style="font-size:.75rem;color:#888;margin-bottom:3px">🏭 FC</div>
                <div style="font-size:1rem;font-weight:700;color:#222">
                  {fc_b:,}<span style="font-size:.85rem;color:#bbb"> → </span>{fc_a:,}</div>
                <div style="font-size:.88rem;font-weight:700;color:{qty_dclr_fc};margin-top:2px">
                  {qty_arr_fc} {abs(fd):,}</div>
              </div>
              <div style="flex:1">
                <div style="font-size:.75rem;color:#888;margin-bottom:3px">🏬 SFC</div>
                <div style="font-size:1rem;font-weight:700;color:#222">
                  {sfc_b:,}<span style="font-size:.85rem;color:#bbb"> → </span>{sfc_a:,}</div>
                <div style="font-size:.88rem;font-weight:700;color:{qty_dclr_sfc};margin-top:2px">
                  {qty_arr_sfc} {abs(sd):,}</div>
              </div>
            </div>
          </div>
          <div style="margin-top:10px;padding-top:10px;border-top:1px solid #f4f4f4">
            <div style="font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;
                 color:#bbb;margin-bottom:8px">Volume in³ (Before → After)</div>
            <div style="display:flex;gap:16px">
              <div style="flex:1">
                <div style="font-size:.75rem;color:#888;margin-bottom:3px">🏭 FC</div>
                <div style="font-size:.9rem;font-weight:600;color:#333">
                  {fc_vb/1000:,.0f}K<span style="font-size:.8rem;color:#bbb"> → </span>{fc_va/1000:,.0f}K</div>
                <div style="font-size:.82rem;font-weight:700;color:{vol_dclr_fc};margin-top:2px">
                  {vol_arr_fc} {abs(fvd)/1000:,.0f}K</div>
              </div>
              <div style="flex:1">
                <div style="font-size:.75rem;color:#888;margin-bottom:3px">🏬 SFC</div>
                <div style="font-size:.9rem;font-weight:600;color:#333">
                  {sfc_vb/1000:,.0f}K<span style="font-size:.8rem;color:#bbb"> → </span>{sfc_va/1000:,.0f}K</div>
                <div style="font-size:.82rem;font-weight:700;color:{vol_dclr_sfc};margin-top:2px">
                  {vol_arr_sfc} {abs(svd)/1000:,.0f}K</div>
              </div>
            </div>
          </div>
        </div>""", unsafe_allow_html=True)

    st.divider()

    # ── Section 3: SKU Distribution — Before vs After ─────
    st.markdown("#### SKU Distribution — Before vs After")

    ov_rows = dist_table(dists)

    fig_ba = go.Figure()
    fig_ba.add_trace(go.Bar(
        name="Before",
        x=[f"{_LOC_ICONS[r['loc']]} {r['loc']}" for r in ov_rows],
        y=[r["before"] for r in ov_rows],
        marker_color=[_LOC_HEX[r["loc"]] for r in ov_rows],
        opacity=0.45,
        text=[f"{r['before']:,}" for r in ov_rows],
        textposition="outside",
        textfont=dict(size=13),
    ))
    fig_ba.add_trace(go.Bar(
        name="After",
        x=[f"{_LOC_ICONS[r['loc']]} {r['loc']}" for r in ov_rows],
        y=[r["after"] for r in ov_rows],
        marker_color=[_LOC_HEX[r["loc"]] for r in ov_rows],
        opacity=1.0,
        text=[f"{r['after']:,}" for r in ov_rows],
        textposition="outside",
        textfont=dict(size=13, color="#222"),
    ))
    fig_ba.update_layout(
        barmode="group", height=320,
        margin=dict(t=20, b=40, l=0, r=0),
        plot_bgcolor="white",
        yaxis=dict(showgrid=True, gridcolor="#f0f0f0", title="SKU Count", tickformat=","),
        xaxis=dict(showgrid=False, tickfont=dict(size=13)),
        legend=dict(orientation="h", x=1, xanchor="right", y=1.08, font=dict(size=13)),
        bargap=0.35, bargroupgap=0.05,
    )
    st.plotly_chart(fig_ba, use_container_width=True)

    dcols = st.columns(len(ov_rows))
    for col, row in zip(dcols, ov_rows):
        loc   = row["loc"]
        b, a  = row["before"], row["after"]
        delta = row["delta"]
        dclr  = "#27ae60" if delta>0 else "#e74c3c" if delta<0 else "#aaa"
        arr   = "▲" if delta>0 else "▼" if delta<0 else "─"
        col.markdown(f"""
        <div style="border-top:3px solid {_LOC_HEX[loc]};padding:10px 4px 4px 4px;text-align:center">
          <div style="font-size:.82rem;color:#666;margin-bottom:6px;font-weight:600">
            {_LOC_ICONS[loc]} {loc}</div>
          <div style="font-size:.9rem;color:#444">
            <span style="opacity:.6">{b:,}</span>
            <span style="color:#ddd;padding:0 5px">→</span>
            <span style="font-weight:700;color:#222">{a:,}</span>
          </div>
          <div style="font-size:.9rem;font-weight:700;color:{dclr};margin-top:4px">
            {arr} {abs(delta):,}</div>
        </div>""", unsafe_allow_html=True)

    st.divider()

    # ── Section 4: SKU Changes ────────────────────────────
    st.markdown("#### SKU-level Quantity Changes")

    kf1, kf2, _ = st.columns([1, 1, 2])
    seg_f = kf1.selectbox("Tier", ["ALL","TOP","MIDDLE","TAIL"],
                           key="ba_seg", label_visibility="collapsed")
    act_f = kf2.selectbox("Action", ["ALL","DC_TO_FC","FC_TO_DC","KEEP"],
                           key="ba_act", label_visibility="collapsed")

    show_ba = df_ideal.copy()
    if seg_f != "ALL": show_ba = show_ba[show_ba["seg"] == seg_f]
    if act_f != "ALL": show_ba = show_ba[show_ba["action"] == act_f]
    show_ba = show_ba[show_ba["action"] != "KEEP"]  # 默认只显示有动作的
    show_ba = show_ba.sort_values("priority", ascending=True)
    show_ba["fc_change"]   = show_ba["fc_ideal"]  - show_ba["fc_qty"]
    show_ba["sfc_change"]  = show_ba["sfc_ideal"] - show_ba["sfc_qty"]
    show_ba["item_number"] = show_ba["item_number"].astype(str)

    st.caption(f"{len(show_ba):,} SKUs with action (KEEP excluded) — sorted by priority")
    st.dataframe(
        show_ba[["item_number","seg","biz_type","action","reason",
                 "fc_d2s","avg_daily",
                 "fc_qty","fc_ideal","fc_change",
                 "sfc_qty","sfc_ideal","sfc_change",
                 "transfer_qty"]].rename(columns={
            "item_number": "SKU",    "seg": "Tier",   "biz_type": "Biz",
            "action":      "Action", "reason": "Reason",
            "fc_d2s":      "FC D2S", "avg_daily": "Avg Daily",
            "fc_qty":      "FC Before",  "fc_ideal":    "FC After",  "fc_change":   "FC Δ",
            "sfc_qty":     "SFC Before", "sfc_ideal":   "SFC After", "sfc_change":  "SFC Δ",
            "transfer_qty":"Transfer Qty",
        }),
        use_container_width=True, hide_index=True, height=420,
        column_config={
            "SKU":          st.column_config.TextColumn(width="medium"),
            "Tier":         st.column_config.TextColumn(width="small"),
            "Biz":          st.column_config.TextColumn(width="small"),
            "Action":       st.column_config.TextColumn(width="medium"),
            "Reason":       st.column_config.TextColumn(width="large"),
            "FC D2S":       st.column_config.NumberColumn(format="%.1f"),
            "Avg Daily":    st.column_config.NumberColumn(format="%.1f"),
            "FC Before":    st.column_config.NumberColumn(format="%d"),
            "FC After":     st.column_config.NumberColumn(format="%d"),
            "FC Δ":         st.column_config.NumberColumn(format="%+d"),
            "SFC Before":   st.column_config.NumberColumn(format="%d"),
            "SFC After":    st.column_config.NumberColumn(format="%d"),
            "SFC Δ":        st.column_config.NumberColumn(format="%+d"),
            "Transfer Qty": st.column_config.NumberColumn(format="%d"),
        }
    )
    st.download_button("⬇ Download", show_ba.to_csv(index=False),
                       "before_vs_after.csv", "text/csv")

# ── Capacity Tab ──────────────────────────────────────────────
with page_capacity:
    st.markdown("### 📦 Warehouse Capacity")
    st.caption("Real-time from MySQL · wh=002 · location_type 3/4 · excl. size 13,33")

    df_cap, cap_err = load_capacity()

    if cap_err:
        st.error(f"Cannot load capacity data: {cap_err}")
    else:
        for c in ["allowable_cap", "used_volume", "free_volume"]:
            df_cap[c] = pd.to_numeric(df_cap[c], errors="coerce").fillna(0)

        def _fv(v):
            if abs(v) >= 1_000_000: return f"{v/1_000_000:.1f}M"
            if abs(v) >= 1_000:     return f"{v/1_000:.0f}K"
            return f"{v:.0f}"

        # ── Summary cards: FC / SFC ───────────────────────────
        st.markdown("#### Summary")
        cc1, cc2 = st.columns(2)

        for col, zone, bg in [(cc1, "FC", "#3d6fbe"), (cc2, "SFC", "#2e8b57")]:
            sub = df_cap[df_cap["zone"] == zone]
            total_cap  = sub["allowable_cap"].sum()
            total_used = sub["used_volume"].sum()
            total_free = sub["free_volume"].sum()
            util       = total_used / max(total_cap, 1) * 100

            # per loc_type breakdown
            by_type = sub.groupby("loc_type")[["allowable_cap","used_volume","free_volume"]].sum()
            rows_html = ""
            for lt, row in by_type.iterrows():
                u = row["used_volume"] / max(row["allowable_cap"], 1) * 100
                bar_clr = "#e74c3c" if u > 85 else "#F0A500" if u > 60 else "#27ae60"
                rows_html += (
                    f"<div style='margin-top:10px'>"
                    f"  <div style='display:flex;justify-content:space-between;"
                    f"       font-size:.82rem;margin-bottom:4px'>"
                    f"    <span style='font-weight:600'>{lt}</span>"
                    f"    <span style='color:#888'>{_fv(row['used_volume'])} / {_fv(row['allowable_cap'])} "
                    f"      &nbsp;<b style='color:{bar_clr}'>{u:.0f}%</b></span>"
                    f"  </div>"
                    f"  <div style='background:#f0f0f0;border-radius:99px;height:8px'>"
                    f"    <div style='background:{bar_clr};width:{min(u,100):.0f}%;"
                    f"         height:8px;border-radius:99px'></div>"
                    f"  </div>"
                    f"</div>"
                )

            util_clr = "#e74c3c" if util > 85 else "#F0A500" if util > 60 else "#27ae60"
            col.markdown(f"""
            <div style="background:{bg};border-radius:12px;padding:20px 24px;color:#fff">
              <div style="font-size:.75rem;opacity:.7;text-transform:uppercase;
                   letter-spacing:.06em;margin-bottom:8px">{zone}</div>
              <div style="display:flex;gap:24px;align-items:flex-end;margin-bottom:12px">
                <div>
                  <div style="font-size:.65rem;opacity:.6">Allowable Cap</div>
                  <div style="font-size:1.6rem;font-weight:800">{_fv(total_cap)}</div>
                </div>
                <div>
                  <div style="font-size:.65rem;opacity:.6">Used</div>
                  <div style="font-size:1.6rem;font-weight:800">{_fv(total_used)}</div>
                </div>
                <div>
                  <div style="font-size:.65rem;opacity:.6">Free</div>
                  <div style="font-size:1.6rem;font-weight:800">{_fv(total_free)}</div>
                </div>
                <div style="margin-left:auto;background:rgba(255,255,255,.2);
                     border-radius:10px;padding:8px 16px;text-align:center">
                  <div style="font-size:1.4rem;font-weight:800">{util:.0f}%</div>
                  <div style="font-size:.7rem;opacity:.7">Utilization</div>
                </div>
              </div>
            </div>""", unsafe_allow_html=True)
            col.markdown(rows_html, unsafe_allow_html=True)

        st.divider()

        # ── Detail table ──────────────────────────────────────
        st.markdown("#### Detail by Zone / Location Type / Storage Type")

        # show both FC and SFC detail
        show_cap = df_cap.copy()
        show_cap["size"] = show_cap["description"].fillna("")
        show_cap["util%"]      = (show_cap["used_volume"] / show_cap["allowable_cap"].replace(0,1) * 100).round(1)
        show_cap["allowable_cap"] = show_cap["allowable_cap"].round(0).astype(int)
        show_cap["used_volume"]   = show_cap["used_volume"].round(0).astype(int)
        show_cap["free_volume"]   = show_cap["free_volume"].round(0).astype(int)

        # filter
        cap_f1, cap_f2 = st.columns([1, 1])
        zone_f = cap_f1.selectbox("Zone", ["ALL", "FC", "SFC"], key="cap_zone",
                                   label_visibility="collapsed")
        type_f = cap_f2.selectbox("Location Type", ["ALL", "Bin", "Stock"], key="cap_type",
                                   label_visibility="collapsed")
        if zone_f != "ALL": show_cap = show_cap[show_cap["zone"] == zone_f]
        if type_f != "ALL": show_cap = show_cap[show_cap["loc_type"] == type_f]

        st.dataframe(
            show_cap[["zone","loc_type","zone_type","storage_type","size","fill_rate",
                       "total_locs","allowable_cap","used_volume","free_volume","util%"]].rename(columns={
                "zone":          "Zone",
                "loc_type":      "Loc Type",
                "zone_type":     "Zone Type",
                "storage_type":  "Storage Type",
                "size":          "Size",
                "fill_rate":     "Fill Rate",
                "total_locs":    "Locations",
                "allowable_cap": "Allowable Cap (in³)",
                "used_volume":   "Used (in³)",
                "free_volume":   "Free (in³)",
                "util%":         "Util %",
            }),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Zone":               st.column_config.TextColumn(width="small"),
                "Loc Type":           st.column_config.TextColumn(width="small"),
                "Zone Type":          st.column_config.TextColumn(width="medium"),
                "Storage Type":       st.column_config.TextColumn(width="medium"),
                "Size":               st.column_config.TextColumn(width="medium"),
                "Fill Rate":          st.column_config.NumberColumn(format="%.2f"),
                "Locations":          st.column_config.NumberColumn(format="%d"),
                "Allowable Cap (in³)":st.column_config.NumberColumn(format="%d"),
                "Used (in³)":         st.column_config.NumberColumn(format="%d"),
                "Free (in³)":         st.column_config.NumberColumn(format="%d"),
                "Util %":             st.column_config.ProgressColumn(
                                          "Util %", min_value=0, max_value=100, format="%.1f%%"),
            }
        )

        # ── Utilization bar chart ─────────────────────────────
        st.markdown("#### Utilization by Zone Type & Storage Type")
        chart_df = df_cap.copy()
        chart_df["label"] = chart_df["zone_type"] + " · " + chart_df["storage_type"] + " · " + chart_df["description"].fillna("")
        chart_df["util%"]  = (chart_df["used_volume"] / chart_df["allowable_cap"].replace(0,1) * 100).round(1)
        chart_df["color"]  = chart_df["zone"].map({"FC": "#5B8DEF", "SFC": "#34C4A4"})
        chart_df = chart_df.sort_values(["zone","loc_type","storage_type"])

        fig_cap = go.Figure()
        for zone, clr in [("FC","#5B8DEF"), ("SFC","#34C4A4")]:
            sub = chart_df[chart_df["zone"]==zone]
            fig_cap.add_trace(go.Bar(
                name=zone,
                x=sub["label"],
                y=sub["util%"],
                marker_color=clr,
                text=sub["util%"].apply(lambda v: f"{v:.0f}%"),
                textposition="outside",
            ))
        fig_cap.add_hline(y=80, line_dash="dash", line_color="#e74c3c",
                          annotation_text="80% warning", annotation_position="right")
        fig_cap.update_layout(
            barmode="group", height=340,
            margin=dict(t=20, b=60, l=0, r=0),
            plot_bgcolor="white",
            yaxis=dict(title="Utilization %", range=[0,110], gridcolor="#f0f0f0"),
            xaxis=dict(showgrid=False, tickangle=-30),
            legend=dict(orientation="h", x=1, xanchor="right", y=1.08),
        )
        st.plotly_chart(fig_cap, use_container_width=True)

# ── Planning Tab ──────────────────────────────────────────────
with page_planning:
    st.markdown("### 📋 30-Day Transfer Planning Simulator")
    st.caption("Simulate how many SKUs can be transferred within the plan window given daily capacity constraints.")

    page_size = 100  # rows per table

    def paginate(df, key, page_size=100):
        """Show paginated dataframe with prev/next controls."""
        total = len(df)
        n_pages = max(1, (total + page_size - 1) // page_size)
        if n_pages == 1:
            return df
        col_l, col_m, col_r = st.columns([1, 2, 1])
        page = col_m.number_input(f"Page (1-{n_pages})", min_value=1,
                                   max_value=n_pages, value=1, step=1,
                                   key=f"page_{key}", label_visibility="collapsed")
        col_l.markdown(f"<div style='padding-top:6px;font-size:.82rem;color:#888'>"
                       f"Showing {(page-1)*page_size+1}–{min(page*page_size,total)} of {total:,}</div>",
                       unsafe_allow_html=True)
        return df.iloc[(page-1)*page_size : page*page_size]

    # ── Parameter sliders ────────────────────────────────────
    with st.expander("⚙️ Parameters", expanded=True):
        col_a, col_b, col_c = st.columns(3)

        with col_a:
            st.markdown("**Workforce & Capacity**")
            workers_out  = st.slider("Workers FC→DC",            1,  20,  5,   step=1,  key="workers_out")
            prod_out     = st.slider("SKU/person/day (FC→DC)",  40, 120, 70,   step=5,  key="prod_out",
                                     help="FC→DC: move out / evict, ~60-80 SKU/person/day")
            workers_in   = st.slider("Workers DC→FC",            1,  20,  5,   step=1,  key="workers_in")
            prod_in      = st.slider("SKU/person/day (DC→FC)",  40, 120, 65,   step=5,  key="prod_in",
                                     help="DC→FC: replenish / putaway, ~60-70 SKU/person/day")
            tasks_pd     = st.slider("Tasks/person/day",        50, 200, 100,  step=10, key="tasks_pd",
                                     help="~100 tasks/person/day")
            pallets_pt   = st.slider("Pallets/task",             1,   5,   2,  step=1,  key="pallets_pt",
                                     help="2-3 pallets/task")
            p_days       = st.slider("Plan window (days)",       7,  90,  30,  step=1,  key="p_days")

        with col_b:
            st.markdown("**Inventory Targets (D2S)**")
            p_top_target = st.slider("TOP target D2S",    7,  60, 28, key="p_top_target")
            p_top_trig   = st.slider("TOP trigger D2S <", 7,  60, 28, key="p_top_trig")
            p_mid_target = st.slider("MID target D2S",    7,  60, 28, key="p_mid_target")
            p_mid_rout   = st.slider("MID routine D2S ≤", 7,  60, 28, key="p_mid_rout")
            p_mid_evict  = st.slider("MID evict D2S >",   14, 90, 30, key="p_mid_evict")

        with col_c:
            st.markdown("**Other**")
            p_min_loc    = st.slider("Min loc qty (skip if <)", 1, 20, 5, key="p_min_loc")

        sku_cap_out    = workers_out * prod_out
        pallet_cap_out = workers_out * tasks_pd * pallets_pt
        daily_cap_out  = min(sku_cap_out, pallet_cap_out)

        sku_cap_in     = workers_in * prod_in
        pallet_cap_in  = workers_in * tasks_pd * pallets_pt
        daily_cap_in   = min(sku_cap_in, pallet_cap_in)

        # ── Derived daily caps (SKU only) ───────────────────
        sku_cap_out    = workers_out * prod_out
        pallet_cap_out = workers_out * tasks_pd * pallets_pt
        daily_cap_out  = min(sku_cap_out, pallet_cap_out)

        sku_cap_in     = workers_in * prod_in
        pallet_cap_in  = workers_in * tasks_pd * pallets_pt
        daily_cap_in   = min(sku_cap_in, pallet_cap_in)

        binding_out = "SKU" if sku_cap_out <= pallet_cap_out else "Pallet"
        binding_in  = "SKU" if sku_cap_in  <= pallet_cap_in  else "Pallet"

        st.markdown(f"""
        <div style="background:#f8f9ff;border-radius:8px;padding:10px 16px;
             font-size:.82rem;color:#555;margin-top:8px">
          📊 <b>Derived daily caps:</b><br>
          FC→DC: <b style='color:#e74c3c'>{daily_cap_out:,} SKU/day</b>
            &nbsp;({workers_out}w × {prod_out} SKU={sku_cap_out:,} &nbsp;|&nbsp;
            {workers_out}w × {tasks_pd} tasks × {pallets_pt} pal={pallet_cap_out:,})
            &nbsp;→ <b>binding: {binding_out}</b><br>
          DC→FC: <b style='color:#27ae60'>{daily_cap_in:,} SKU/day</b>
            &nbsp;({workers_in}w × {prod_in} SKU={sku_cap_in:,} &nbsp;|&nbsp;
            {workers_in}w × {tasks_pd} tasks × {pallets_pt} pal={pallet_cap_in:,})
            &nbsp;→ <b>binding: {binding_in}</b><br>
          Total pallets/day: <b>{pallet_cap_out + pallet_cap_in:,}</b>
          &nbsp;(6ft pallet = 110,592 in³ usable)
        </div>
        """, unsafe_allow_html=True)

        plan_params = dict(
            daily_cap_in       = daily_cap_in,
            daily_cap_out      = daily_cap_out,
            daily_pallet_cap_out = pallet_cap_out,  # workers_out pallet cap
            daily_pallet_cap_in  = pallet_cap_in,   # workers_in pallet cap
            plan_days          = p_days,
            top_trigger_d2s    = p_top_trig,
            top_target_d2s     = p_top_target,
            mid_routine_d2s    = p_mid_rout,
            mid_emergency_d2s  = 14,
            mid_evict_d2s      = p_mid_evict,
            mid_target_d2s     = p_mid_target,
            safety_d2s         = 7,
            min_loc_qty        = p_min_loc,
            pallet_vol_in3     = 106_000,
        )

    with st.spinner("Running..."):
        df_sched, daily, smry = run_planning(df, plan_params)

    if not smry:
        st.warning("No transfers needed with current parameters.")
    else:
        # ── KPI cards ─────────────────────────────────────────
        st.markdown("#### Results")
        k1, k2, k3, k4, k5 = st.columns(5)

        def _kpi(col, title, val, sub, color="#333"):
            col.markdown(f"""
            <div style="border-radius:10px;border:1.5px solid #eee;padding:16px 18px;
                 background:#fafafa;text-align:center">
              <div style="font-size:.75rem;color:#aaa;text-transform:uppercase;
                   letter-spacing:.05em;margin-bottom:6px">{title}</div>
              <div style="font-size:1.8rem;font-weight:800;color:{color}">{val}</div>
              <div style="font-size:.8rem;color:#888;margin-top:4px">{sub}</div>
            </div>""", unsafe_allow_html=True)

        _kpi(k1, "DC→FC Complete",
             f"{smry['pct_in']:.0f}%",
             f"{smry['done_in']:,} / {smry['total_in']:,} SKUs",
             "#27ae60" if smry["pct_in"]==100 else "#F0A500")
        _kpi(k2, "FC→DC Complete",
             f"{smry['pct_out']:.0f}%",
             f"{smry['done_out']:,} / {smry['total_out']:,} SKUs",
             "#27ae60" if smry["pct_out"]==100 else "#e74c3c" if smry["pct_out"]<50 else "#F0A500")
        _kpi(k3, "Pallets / Day",
             f"{smry['pallets_per_day']:.0f}",
             f"{smry['total_pallets']:,} total pallets")
        _kpi(k4, "Remaining after plan",
             f"{smry['rem_in']+smry['rem_out']:,}",
             f"↑{smry['rem_in']:,} in  ↓{smry['rem_out']:,} out SKUs",
             "#e74c3c" if smry["rem_in"]+smry["rem_out"]>0 else "#27ae60")

        def _fv2(v):
            if abs(v) >= 1_000_000: return f"{v/1_000_000:.1f}M in³"
            return f"{v/1_000:.0f}K in³"
        bal = smry["vol_balance"]
        _kpi(k5, "Vol Balance (Out−In)",
             _fv2(bal),
             "Released > Moved In ✅" if bal >= 0 else "⚠️ Moved In > Released",
             "#27ae60" if bal >= 0 else "#e74c3c")

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Daily charts ──────────────────────────────────────
        st.markdown("#### Daily Transfer")

        if not daily.empty:
            # per-seg daily count
            seg_daily = df_sched.groupby(["day","action","seg"]).size().reset_index(name="sku_count")

            pivot_sku = daily.pivot_table(
                index=["day","date"], columns="action",
                values="sku_count", aggfunc="sum", fill_value=0
            ).reset_index()
            pivot_sku.columns.name = None

            pivot_vol = daily.pivot_table(
                index=["day","date"], columns="action",
                values="transfer_vol", aggfunc="sum", fill_value=0
            ).reset_index()
            pivot_vol.columns.name = None

            ch1, ch2, ch3 = st.columns(3)

            # SKU by seg — shows priority order (TAIL first in FC→DC, TOP first in DC→FC)
            with ch1:
                fig_seg = go.Figure()
                seg_colors = {"TAIL":"#8C8C8C","MIDDLE":"#F0A500","TOP":"#5B8DEF"}
                for seg_v in ["TAIL","MIDDLE","TOP"]:
                    for act, dash in [("FC_TO_DC","solid"),("DC_TO_FC","dot")]:
                        sub = seg_daily[(seg_daily["seg"]==seg_v)&(seg_daily["action"]==act)]
                        if sub.empty: continue
                        fig_seg.add_trace(go.Bar(
                            name=f"{seg_v} {act.replace('_TO_','→')}",
                            x=sub["day"], y=sub["sku_count"],
                            marker_color=seg_colors[seg_v],
                            opacity=0.9 if act=="FC_TO_DC" else 0.5,
                        ))
                fig_seg.update_layout(
                    barmode="stack", height=280,
                    title="SKU by Tier & Direction",
                    margin=dict(t=35,b=30,l=0,r=0), plot_bgcolor="white",
                    yaxis=dict(title="SKUs", gridcolor="#f0f0f0"),
                    xaxis=dict(title="Day", showgrid=False),
                    legend=dict(orientation="v", x=1.01, y=1, font=dict(size=10)),
                    showlegend=True,
                )
                st.plotly_chart(fig_seg, use_container_width=True)

            with ch2:
                fig_sku = go.Figure()
                for act, clr, name in [
                    ("FC_TO_DC", "#e74c3c", "FC→DC"),
                    ("DC_TO_FC", "#27ae60", "DC→FC"),
                ]:
                    if act in pivot_sku.columns:
                        fig_sku.add_trace(go.Bar(
                            name=name, x=pivot_sku["day"],
                            y=pivot_sku[act], marker_color=clr, opacity=0.85,
                        ))
                fig_sku.update_layout(
                    barmode="group", height=280, title="Total SKUs / Day",
                    margin=dict(t=35,b=30,l=0,r=0), plot_bgcolor="white",
                    yaxis=dict(title="SKUs", gridcolor="#f0f0f0"),
                    xaxis=dict(title="Day", showgrid=False),
                    legend=dict(orientation="h", x=1, xanchor="right", y=1.1),
                )
                st.plotly_chart(fig_sku, use_container_width=True)

            with ch3:
                fig_vol = go.Figure()
                for act, clr, name in [
                    ("FC_TO_DC", "#e74c3c", "FC→DC"),
                    ("DC_TO_FC", "#27ae60", "DC→FC"),
                ]:
                    if act in pivot_vol.columns:
                        fig_vol.add_trace(go.Bar(
                            name=name, x=pivot_vol["day"],
                            y=pivot_vol[act] / 1_000_000,
                            marker_color=clr, opacity=0.85,
                        ))
                fig_vol.update_layout(
                    barmode="group", height=280, title="Volume (M in³) / Day",
                    margin=dict(t=35,b=30,l=0,r=0), plot_bgcolor="white",
                    yaxis=dict(title="M in³", gridcolor="#f0f0f0"),
                    xaxis=dict(title="Day", showgrid=False),
                    legend=dict(orientation="h", x=1, xanchor="right", y=1.1),
                )
                st.plotly_chart(fig_vol, use_container_width=True)
            st.caption("💡 Left chart shows priority: TAIL processed first in FC→DC, TOP first in DC→FC. Volume decreases as large-volume SKUs are handled first.")

        # ── Cumulative completion + volume balance ────────────
        st.markdown("#### Cumulative Completion & Volume Balance")
        if not df_sched.empty:
            cum_in  = df_sched[df_sched["action"]=="DC_TO_FC"].groupby("day")["transfer_vol"].sum().cumsum()
            cum_out = df_sched[df_sched["action"]=="FC_TO_DC"].groupby("day")["transfer_vol"].sum().cumsum()
            all_days = range(1, p_days + 1)
            cum_in  = cum_in.reindex(all_days,  method="ffill").fillna(0)
            cum_out = cum_out.reindex(all_days, method="ffill").fillna(0)
            cum_bal = cum_out - cum_in  # should stay ≥ 0

            cum_sku_in  = df_sched[df_sched["action"]=="DC_TO_FC"].groupby("day").size().cumsum()
            cum_sku_out = df_sched[df_sched["action"]=="FC_TO_DC"].groupby("day").size().cumsum()
            cum_sku_in  = cum_sku_in.reindex(all_days,  method="ffill").fillna(0)
            cum_sku_out = cum_sku_out.reindex(all_days, method="ffill").fillna(0)

            cum_c1, cum_c2 = st.columns(2)

            with cum_c1:
                st.caption("SKU cumulative")
                fig_cum = go.Figure()
                fig_cum.add_trace(go.Scatter(
                    x=list(all_days), y=cum_sku_in.values,
                    name="DC→FC", line=dict(color="#27ae60", width=2.5),
                    fill="tozeroy", fillcolor="rgba(39,174,96,.08)",
                ))
                fig_cum.add_trace(go.Scatter(
                    x=list(all_days), y=cum_sku_out.values,
                    name="FC→DC", line=dict(color="#e74c3c", width=2.5),
                    fill="tozeroy", fillcolor="rgba(231,76,60,.08)",
                ))
                fig_cum.add_hline(y=smry["total_in"],  line_dash="dash", line_color="#27ae60", opacity=.5,
                                  annotation_text=f"DC→FC target ({smry['total_in']:,})")
                fig_cum.add_hline(y=smry["total_out"], line_dash="dash", line_color="#e74c3c", opacity=.5,
                                  annotation_text=f"FC→DC target ({smry['total_out']:,})")
                fig_cum.update_layout(height=280, margin=dict(t=10,b=30,l=0,r=80),
                    plot_bgcolor="white",
                    yaxis=dict(title="SKUs", gridcolor="#f0f0f0"),
                    xaxis=dict(title="Day", showgrid=False),
                    legend=dict(orientation="h", x=1, xanchor="right", y=1.1))
                st.plotly_chart(fig_cum, use_container_width=True)

            with cum_c2:
                st.caption("Volume balance (cum Out − cum In) — should stay ≥ 0")
                fig_bal = go.Figure()
                fig_bal.add_trace(go.Scatter(
                    x=list(all_days), y=cum_out.values / 1e6,
                    name="Cum FC→DC", line=dict(color="#e74c3c", width=2),
                ))
                fig_bal.add_trace(go.Scatter(
                    x=list(all_days), y=cum_in.values / 1e6,
                    name="Cum DC→FC", line=dict(color="#27ae60", width=2),
                ))
                fig_bal.add_trace(go.Scatter(
                    x=list(all_days), y=cum_bal.values / 1e6,
                    name="Balance (Out−In)", line=dict(color="#3d52d5", width=2.5, dash="dot"),
                    fill="tozeroy", fillcolor="rgba(61,82,213,.06)",
                ))
                fig_bal.add_hline(y=0, line_color="#aaa", line_width=1)
                fig_bal.update_layout(height=280, margin=dict(t=10,b=30,l=0,r=0),
                    plot_bgcolor="white",
                    yaxis=dict(title="M in³", gridcolor="#f0f0f0"),
                    xaxis=dict(title="Day", showgrid=False),
                    legend=dict(orientation="h", x=1, xanchor="right", y=1.1))
                st.plotly_chart(fig_bal, use_container_width=True)

        # ── SKU detail table ──────────────────────────────────
        st.divider()
        st.markdown("#### Scheduled SKUs (Day-level)")

        pf1, pf2, pf3 = st.columns([1, 1, 1])
        day_filter = pf1.selectbox("Day", ["ALL"] + [str(d) for d in range(1, p_days+1)],
                                    key="pf_day", label_visibility="collapsed")
        act_filter = pf2.selectbox("Action", ["ALL","DC_TO_FC","FC_TO_DC"],
                                    key="pf_act", label_visibility="collapsed")
        seg_filter_p = pf3.selectbox("Tier", ["ALL","TOP","MIDDLE","TAIL"],
                                      key="pf_seg", label_visibility="collapsed")

        show_p = df_sched.copy()
        show_p["transfer_vol"] = show_p["transfer_vol"].round(0).astype(int)
        if day_filter != "ALL": show_p = show_p[show_p["day"]==int(day_filter)]
        if act_filter != "ALL": show_p = show_p[show_p["action"]==act_filter]
        if seg_filter_p != "ALL": show_p = show_p[show_p["seg"]==seg_filter_p]

        st.caption(f"{len(show_p):,} records  (showing {min(page_size, len(show_p)):,})")
        st.dataframe(
            paginate(show_p, "sched")[["day","date","item_number","seg","action",
                    "transfer_qty","transfer_vol"]].rename(columns={
                "day":          "Day",
                "date":         "Date",
                "item_number":  "SKU",
                "seg":          "Tier",
                "action":       "Action",
                "transfer_qty": "Qty",
                "transfer_vol": "Volume (in³)",
            }),
            use_container_width=True, hide_index=True, height=400,
            column_config={
                "Day":          st.column_config.NumberColumn(format="%d"),
                "SKU":          st.column_config.TextColumn(width="medium"),
                "Tier":         st.column_config.TextColumn(width="small"),
                "Action":       st.column_config.TextColumn(width="medium"),
                "Qty":          st.column_config.NumberColumn(format="%d"),
                "Volume (in³)": st.column_config.NumberColumn(format="%.0f"),
            }
        )
        st.download_button("⬇ Download Schedule", df_sched.to_csv(index=False),
                           "planning_schedule.csv", "text/csv")

    st.divider()

    # ── MIDDLE FC→DC Location Selection ──────────────────────
    import numpy as np
    st.markdown("#### MIDDLE FC→DC — Location Selection Plan")
    st.caption("Sheet rules: need_minimal_exp=1 → Rule 3/4 (exp-based), else Rule 1/2 (closest/excess)")

    # Get MIDDLE FC→DC SKUs from df (current thresholds)
    df_mid_plan = df.copy()
    df_mid_plan["fc_d2s"] = np.where(
        df_mid_plan["avg_daily_orders"]>0,
        df_mid_plan["fc_qty"]/df_mid_plan["avg_daily_orders"], np.nan
    )
    df_mid_plan["keep_qty"]   = np.where(
        df_mid_plan["avg_daily_orders"]>0,
        np.round(df_mid_plan["avg_daily_orders"]*p_mid_evict), df_mid_plan["fc_qty"]
    )
    df_mid_plan["excess_qty"] = (df_mid_plan["fc_qty"] - df_mid_plan["keep_qty"]).clip(lower=0)

    mid_out_skus_df = df_mid_plan[
        (df_mid_plan["seg"]=="MIDDLE") &
        (df_mid_plan["avg_daily_orders"]>0) &
        (~df_mid_plan["fc_d2s"].isna()) &
        (df_mid_plan["fc_d2s"]>p_mid_evict) &
        (df_mid_plan["excess_qty"]>p_min_loc)
    ][["item_number","excess_qty","keep_qty","avg_daily_orders","unit_volume_cbm"]].copy()
    mid_out_skus_df["item_number"] = mid_out_skus_df["item_number"].astype(str)

    mid_out_skus_df["excess_qty"] = mid_out_skus_df["excess_qty"].round(0).astype(int)
    mid_out_skus_df["keep_qty"]   = mid_out_skus_df["keep_qty"].round(0).astype(int)

    st.caption(f"{len(mid_out_skus_df):,} MIDDLE SKUs need FC→DC  |  "
               f"Total excess: {int(mid_out_skus_df['excess_qty'].sum()):,} units")

    if st.button("🔄 Load MIDDLE Location Plan (MySQL)", key="load_mid_locs"):
        with st.spinner(f"Querying {len(mid_out_skus_df):,} SKU locations from MySQL..."):
            df_lot_mid, lot_err = load_middle_locations(mid_out_skus_df["item_number"].tolist())

        if lot_err:
            st.error(f"MySQL error: {lot_err}")
        elif df_lot_mid is not None:
            with st.spinner("Applying location selection rules..."):
                df_mid_locs = select_middle_locations(df_lot_mid, mid_out_skus_df)
            st.session_state["mid_loc_plan"] = df_mid_locs

    if "mid_loc_plan" in st.session_state:
        df_mid_locs = st.session_state["mid_loc_plan"]
        if not df_mid_locs.empty:
            # Rule summary
            rule_sum = df_mid_locs.groupby("rule")[["item_number","qty_to_transfer"]].agg(
                skus=("item_number","nunique"), qty=("qty_to_transfer","sum")).reset_index()
            rule_sum["qty"] = rule_sum["qty"].round(0).astype(int)
            r1, r2 = st.columns(2)
            with r1:
                st.markdown("**By Rule**")
                st.dataframe(rule_sum, use_container_width=True, hide_index=True)
            with r2:
                st.markdown("**By Loc Type**")
                loc_sum = df_mid_locs.groupby("loc_type")[["qty_to_transfer"]].agg(
                    locs=("qty_to_transfer","count"), qty=("qty_to_transfer","sum")).reset_index()
                loc_sum["qty"] = loc_sum["qty"].round(0).astype(int)
                st.dataframe(loc_sum, use_container_width=True, hide_index=True)

            # Detail table
            mf1, mf2 = st.columns([1,1])
            rule_f = mf1.selectbox("Rule", ["ALL"]+sorted(df_mid_locs["rule"].unique().tolist()),
                                    key="mid_rule_f", label_visibility="collapsed")
            type_f = mf2.selectbox("Loc Type", ["ALL","Bin","Stock"],
                                    key="mid_type_f", label_visibility="collapsed")
            show_mid = df_mid_locs.copy()
            if rule_f != "ALL": show_mid = show_mid[show_mid["rule"]==rule_f]
            if type_f != "ALL": show_mid = show_mid[show_mid["loc_type"]==type_f]

            st.caption(f"{len(show_mid):,} location moves  (showing {min(page_size, len(show_mid)):,})")
            st.dataframe(paginate(show_mid, "mid_out")[["item_number","location_no","loc_type","loc_qty",
                                    "qty_to_transfer","exp_date","rule","excess_qty"]],
                         use_container_width=True, hide_index=True, height=350)
            st.download_button("⬇ Download Middle Plan",
                               df_mid_locs.to_csv(index=False),
                               "middle_fc_to_dc_plan.csv", "text/csv")

    st.divider()

    # ── TAIL FC→DC Location Plan ──────────────────────────────
    st.markdown("#### TAIL FC→DC — Location-level Plan")
    st.caption("Full clear: all FC locations per SKU. Sorted by location_no for sequential ops.")

    tail_skus_df = df[
        (df["seg"]=="TAIL") & (df["fc_qty"]>0)
    ][["item_number","fc_qty","avg_daily_orders","unit_volume_cbm"]].copy()
    tail_skus_df["item_number"] = tail_skus_df["item_number"].astype(str)

    st.caption(f"{len(tail_skus_df):,} TAIL SKUs to clear from FC  |  "
               f"Total FC qty: {int(tail_skus_df['fc_qty'].sum()):,} units")

    if st.button("🔄 Load TAIL Location Plan (MySQL)", key="load_tail_locs"):
        with st.spinner(f"Querying {len(tail_skus_df):,} TAIL SKU locations..."):
            df_lot_tail, tail_err = load_tail_locations(tail_skus_df["item_number"].tolist())
        if tail_err:
            st.error(f"MySQL error: {tail_err}")
        elif df_lot_tail is not None:
            df_tail_locs = select_tail_locations(df_lot_tail)
            # add unit_vol for volume calc
            uv_map = tail_skus_df.set_index("item_number")["unit_volume_cbm"].to_dict()
            df_tail_locs["unit_vol"] = df_tail_locs["item_number"].map(uv_map).fillna(0)
            df_tail_locs["transfer_vol"] = (df_tail_locs["qty_to_transfer"] * df_tail_locs["unit_vol"]).round(0).astype(int)
            st.session_state["tail_loc_plan"] = df_tail_locs

    if "tail_loc_plan" in st.session_state:
        df_tail_locs = st.session_state["tail_loc_plan"]
        if not df_tail_locs.empty:
            t1, t2, t3 = st.columns(3)
            t1.metric("Total Locations", f"{df_tail_locs['location_no'].nunique():,}")
            t2.metric("Total Qty", f"{int(df_tail_locs['qty_to_transfer'].sum()):,}")
            t3.metric("Total Volume", f"{df_tail_locs['transfer_vol'].sum()/1e6:.1f}M in³")

            # Loc type breakdown
            lt_sum = df_tail_locs.groupby("loc_type").agg(
                locs=("location_no","nunique"),
                qty =("qty_to_transfer","sum"),
            ).reset_index()
            lt_sum["qty"] = lt_sum["qty"].round(0).astype(int)
            st.dataframe(lt_sum, use_container_width=True, hide_index=True)

            # Filter + detail
            tf1, tf2 = st.columns([1,1])
            t_type_f  = tf1.selectbox("Loc Type", ["ALL","Bin","Stock"], key="t_type_f",
                                       label_visibility="collapsed")
            t_sku_f   = tf2.text_input("Filter SKU", "", key="t_sku_f",
                                        placeholder="item_number...")
            show_tail = df_tail_locs.copy()
            if t_type_f != "ALL": show_tail = show_tail[show_tail["loc_type"]==t_type_f]
            if t_sku_f:           show_tail = show_tail[show_tail["item_number"].str.contains(t_sku_f)]

            st.caption(f"{len(show_tail):,} location moves  (showing {min(page_size, len(show_tail)):,})")
            st.dataframe(
                paginate(show_tail, "tail")[["item_number","location_no","loc_type","qty_to_transfer",
                            "transfer_vol","exp_date","rule"]],
                use_container_width=True, hide_index=True, height=400,
                column_config={
                    "item_number":    st.column_config.TextColumn("SKU", width="medium"),
                    "location_no":    st.column_config.TextColumn("Location", width="medium"),
                    "loc_type":       st.column_config.TextColumn("Type", width="small"),
                    "qty_to_transfer":st.column_config.NumberColumn("Qty", format="%d"),
                    "transfer_vol":   st.column_config.NumberColumn("Vol (in³)", format="%d"),
                    "exp_date":       st.column_config.TextColumn("Exp Date"),
                    "rule":           st.column_config.TextColumn("Rule"),
                }
            )
            st.download_button("⬇ Download TAIL Plan",
                               df_tail_locs.to_csv(index=False),
                               "tail_fc_to_dc_plan.csv", "text/csv")

    st.divider()

    # ── MIDDLE DC→FC Location Plan ────────────────────────────
    st.markdown("#### MIDDLE DC→FC — Location-level Plan (SFC → FC)")
    st.caption("Routine replenish: whole-location transfer from SFC until 28D2S reached.")

    mid_in_skus_df = df.copy()
    mid_in_skus_df["fc_d2s"] = np.where(
        mid_in_skus_df["avg_daily_orders"]>0,
        mid_in_skus_df["fc_qty"]/mid_in_skus_df["avg_daily_orders"], np.nan
    )
    mid_in_skus_df = mid_in_skus_df[
        (mid_in_skus_df["seg"]=="MIDDLE") &
        (mid_in_skus_df["avg_daily_orders"]>0) &
        (~mid_in_skus_df["fc_d2s"].isna()) &
        (mid_in_skus_df["fc_d2s"] <= p_mid_rout) &
        (mid_in_skus_df["sfc_qty"] > 0)
    ].copy()
    mid_in_skus_df["item_number"] = mid_in_skus_df["item_number"].astype(str)
    mid_in_skus_df["needed_qty"]  = (mid_in_skus_df["avg_daily_orders"] * p_mid_target - mid_in_skus_df["fc_qty"]).clip(lower=0)
    mid_in_skus_df = mid_in_skus_df[mid_in_skus_df["needed_qty"] > 0]

    st.caption(f"{len(mid_in_skus_df):,} MIDDLE SKUs need DC→FC  |  "
               f"Total needed qty: {int(mid_in_skus_df['needed_qty'].sum()):,} units")

    if st.button("🔄 Load MIDDLE DC→FC Location Plan (MySQL)", key="load_mid_in_locs"):
        with st.spinner(f"Querying {len(mid_in_skus_df):,} MIDDLE SKU SFC locations..."):
            df_sfc_mid, mid_in_err = load_top_sfc_locations(mid_in_skus_df["item_number"].tolist())
        if mid_in_err:
            st.error(f"MySQL error: {mid_in_err}")
        elif df_sfc_mid is not None:
            # reuse select_top_locations logic (same: pick min SFC locs to reach target)
            df_mid_in_locs = select_top_locations(df_sfc_mid, mid_in_skus_df)
            uv_map = mid_in_skus_df.set_index("item_number")["unit_volume_cbm"].to_dict()
            df_mid_in_locs["unit_vol"]     = df_mid_in_locs["item_number"].map(uv_map).fillna(0)
            df_mid_in_locs["transfer_vol"] = (df_mid_in_locs["qty_to_transfer"] * df_mid_in_locs["unit_vol"]).round(0).astype(int)
            df_mid_in_locs["rule"] = "MID_DC_TO_FC"
            st.session_state["mid_in_loc_plan"] = df_mid_in_locs

    if "mid_in_loc_plan" in st.session_state:
        df_mid_in_locs = st.session_state["mid_in_loc_plan"]
        if not df_mid_in_locs.empty:
            mi1, mi2, mi3 = st.columns(3)
            mi1.metric("Total Locations", f"{df_mid_in_locs['location_no'].nunique():,}")
            mi2.metric("Total Qty", f"{int(df_mid_in_locs['qty_to_transfer'].sum()):,}")
            mi3.metric("SKUs covered", f"{df_mid_in_locs['item_number'].nunique():,}")

            lt_mid_in = df_mid_in_locs.groupby("loc_type").agg(
                locs=("location_no","nunique"), qty=("qty_to_transfer","sum")).reset_index()
            lt_mid_in["qty"] = lt_mid_in["qty"].round(0).astype(int)
            st.dataframe(lt_mid_in, use_container_width=True, hide_index=True)

            mif1, mif2 = st.columns([1,1])
            mi_type_f = mif1.selectbox("Loc Type", ["ALL","Bin","Stock"], key="mi_type_f",
                                        label_visibility="collapsed")
            mi_sku_f  = mif2.text_input("Filter SKU", "", key="mi_sku_f",
                                         placeholder="item_number...")
            show_mid_in = df_mid_in_locs.copy()
            if mi_type_f != "ALL": show_mid_in = show_mid_in[show_mid_in["loc_type"]==mi_type_f]
            if mi_sku_f:           show_mid_in = show_mid_in[show_mid_in["item_number"].str.contains(mi_sku_f)]

            st.caption(f"{len(show_mid_in):,} location moves  (showing {min(page_size, len(show_mid_in)):,})")
            st.dataframe(
                paginate(show_mid_in, "mid_in")[["item_number","location_no","loc_type","storage_type",
                              "loc_qty","qty_to_transfer","transfer_vol",
                              "fc_qty_before","target_qty","rule"]],
                use_container_width=True, hide_index=True, height=400,
                column_config={
                    "item_number":    st.column_config.TextColumn("SKU", width="medium"),
                    "location_no":    st.column_config.TextColumn("Location", width="medium"),
                    "loc_type":       st.column_config.TextColumn("Type", width="small"),
                    "storage_type":   st.column_config.TextColumn("Storage", width="medium"),
                    "loc_qty":        st.column_config.NumberColumn("Loc Qty", format="%d"),
                    "qty_to_transfer":st.column_config.NumberColumn("Transfer Qty", format="%d"),
                    "transfer_vol":   st.column_config.NumberColumn("Vol (in³)", format="%d"),
                    "fc_qty_before":  st.column_config.NumberColumn("FC Before", format="%d"),
                    "target_qty":     st.column_config.NumberColumn("28D Target", format="%d"),
                }
            )
            st.download_button("⬇ Download MIDDLE DC→FC Plan",
                               df_mid_in_locs.to_csv(index=False),
                               "middle_dc_to_fc_plan.csv", "text/csv")
    st.markdown("#### TOP DC→FC — Location-level Plan (SFC → FC)")
    st.caption("Whole-location transfer from SFC. Min locations to reach 28D2S. Sorted by location_no.")

    top_skus_df = df[
        (df["seg"]=="TOP") &
        (df["avg_daily_orders"]>0) &
        (df["sfc_qty"]>0)
    ].copy()
    top_skus_df["fc_d2s"] = top_skus_df["fc_qty"] / top_skus_df["avg_daily_orders"]
    top_skus_df = top_skus_df[top_skus_df["fc_d2s"] < p_top_trig].copy()
    top_skus_df["item_number"] = top_skus_df["item_number"].astype(str)
    top_skus_df["needed_qty"]  = (top_skus_df["avg_daily_orders"] * p_top_target - top_skus_df["fc_qty"]).clip(lower=0)

    st.caption(f"{len(top_skus_df):,} TOP SKUs need DC→FC  |  "
               f"Total needed qty: {int(top_skus_df['needed_qty'].sum()):,} units")

    if st.button("🔄 Load TOP Location Plan (MySQL)", key="load_top_locs"):
        with st.spinner(f"Querying {len(top_skus_df):,} TOP SKU SFC locations..."):
            df_sfc_top, top_err = load_top_sfc_locations(top_skus_df["item_number"].tolist())
        if top_err:
            st.error(f"MySQL error: {top_err}")
        elif df_sfc_top is not None:
            df_top_locs = select_top_locations(df_sfc_top, top_skus_df)
            uv_map = top_skus_df.set_index("item_number")["unit_volume_cbm"].to_dict()
            df_top_locs["unit_vol"]     = df_top_locs["item_number"].map(uv_map).fillna(0)
            df_top_locs["transfer_vol"] = (df_top_locs["qty_to_transfer"] * df_top_locs["unit_vol"]).round(0).astype(int)
            st.session_state["top_loc_plan"] = df_top_locs

    if "top_loc_plan" in st.session_state:
        df_top_locs = st.session_state["top_loc_plan"]
        if not df_top_locs.empty:
            tp1, tp2, tp3 = st.columns(3)
            tp1.metric("Total Locations", f"{df_top_locs['location_no'].nunique():,}")
            tp2.metric("Total Qty", f"{int(df_top_locs['qty_to_transfer'].sum()):,}")
            tp3.metric("SKUs covered", f"{df_top_locs['item_number'].nunique():,}")

            lt_top = df_top_locs.groupby("loc_type").agg(
                locs=("location_no","nunique"), qty=("qty_to_transfer","sum")).reset_index()
            lt_top["qty"] = lt_top["qty"].round(0).astype(int)
            st.dataframe(lt_top, use_container_width=True, hide_index=True)

            tpf1, tpf2 = st.columns([1,1])
            tp_type_f = tpf1.selectbox("Loc Type", ["ALL","Bin","Stock"], key="tp_type_f",
                                        label_visibility="collapsed")
            tp_sku_f  = tpf2.text_input("Filter SKU", "", key="tp_sku_f",
                                         placeholder="item_number...")
            show_top = df_top_locs.copy()
            if tp_type_f != "ALL": show_top = show_top[show_top["loc_type"]==tp_type_f]
            if tp_sku_f:           show_top = show_top[show_top["item_number"].str.contains(tp_sku_f)]

            st.caption(f"{len(show_top):,} location moves  (showing {min(page_size, len(show_top)):,})")
            st.dataframe(
                paginate(show_top, "top")[["item_number","location_no","loc_type","storage_type",
                           "loc_qty","qty_to_transfer","transfer_vol",
                           "fc_qty_before","target_qty","rule"]],
                use_container_width=True, hide_index=True, height=400,
                column_config={
                    "item_number":    st.column_config.TextColumn("SKU", width="medium"),
                    "location_no":    st.column_config.TextColumn("Location", width="medium"),
                    "loc_type":       st.column_config.TextColumn("Type", width="small"),
                    "storage_type":   st.column_config.TextColumn("Storage", width="medium"),
                    "loc_qty":        st.column_config.NumberColumn("Loc Qty", format="%d"),
                    "qty_to_transfer":st.column_config.NumberColumn("Transfer Qty", format="%d"),
                    "transfer_vol":   st.column_config.NumberColumn("Vol (in³)", format="%d"),
                    "fc_qty_before":  st.column_config.NumberColumn("FC Before", format="%d"),
                    "target_qty":     st.column_config.NumberColumn("28D Target", format="%d"),
                    "rule":           st.column_config.TextColumn("Rule"),
                }
            )
            st.download_button("⬇ Download TOP Plan",
                               df_top_locs.to_csv(index=False),
                               "top_dc_to_fc_plan.csv", "text/csv")
