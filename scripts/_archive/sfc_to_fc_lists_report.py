"""
SFC → FC Lists Diagnostic Report (快速版)
==========================================
五个 list:
  TOP_FC_Less3D2S     : TOP, sfc_qty>0, fc_d2s < 3  (紧急，FC 库存不足 3 天)
  TOP_FC_Over3D2S     : TOP, sfc_qty>0, fc_d2s >= 3 (常规，按 EXP→D2S→InvAge 排序)
  MIDDLE_Replen_Emergency : MIDDLE, fc_d2s < 3       (紧急补货)
  MIDDLE_Replen_Routine   : MIDDLE, fc_d2s 3~14, 无更早 EXP  (常规补货)
  MIDDLE_ZonePick         : MIDDLE, fc_d2s >= 3, SFC Bin, sfc_min_exp <= fc_min_exp
  MIDDLE_DateSwitch       : MIDDLE, fc_d2s >= 3, SFC Bin, sfc_min_exp > fc_min_exp

输出：
  results/sfc_to_fc_report_<date>/
    ├── summary.csv
    ├── top_fc_less3d2s_top100.csv
    ├── top_fc_over3d2s_top100.csv
    ├── middle_emergency_top100.csv
    ├── middle_routine_top100.csv
    ├── middle_zonepick_top100.csv
    └── middle_dateswitch_top100.csv

Summary: 全量 SKU
明细:    每个 list 前 100 SKU + SFC 库位

输出：
  results/sfc_to_fc_report_<date>/
    ├── summary.csv
    ├── top_fc_less3d2s_top100.csv
    ├── top_fc_over3d2s_top100.csv
    ├── middle_emergency_top100.csv
    ├── middle_routine_top100.csv
    └── middle_zonepick_top100.csv

运行：
  cd c:\\Project-Z\\NJ_Inventory_Allocation
  python v4_DC_allocation/explore/sfc_to_fc_lists_report.py
"""

import os, sys, pymysql, pandas as pd, numpy as np
from pathlib import Path
from dotenv import load_dotenv
from datetime import date

ROOT = Path(__file__).parent.parent.parent
load_dotenv(dotenv_path=ROOT / ".env")

TODAY = date.today()
OUT_DIR = Path(__file__).parent.parent / "results" / f"sfc_to_fc_report_{TODAY:%Y%m%d}"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TOP_N = 100

P = dict(
    aod_top           = 21,
    ol_top            = 30,
    ol_tail           = 4,
    mid_target_d2s    = 28,
    mid_emergency_d2s = 14,   # List 2 上限
    min_loc_qty       = 5,
)

def mysql_conn():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
        connect_timeout=15,
    )

# ── 1. SKU-level inventory ────────────────────────────────────
print("[ 1/4 ] Loading FC/SFC inventory...", flush=True)
conn = mysql_conn()
with conn.cursor() as cur:
    cur.execute("""
        SELECT wsli.item_number,
            SUM(CASE WHEN wsli.location_no NOT LIKE 'S%%'
                      AND wsli.location_no NOT LIKE 'DC%%' THEN wit.quantity ELSE 0 END) AS fc_qty,
            SUM(CASE WHEN wsli.location_no     LIKE 'S%%'
                      OR wsli.location_no     LIKE 'DC%%' THEN wit.quantity ELSE 0 END) AS sfc_qty
        FROM yamibuy_wh.wh_storage_location_item wsli
        JOIN yamibuy_wh.wh_inventory_transaction wit
            ON wit.item_number=wsli.item_number AND wit.warehouse_number=wsli.warehouse_number
           AND wit.location_no=wsli.location_no
        JOIN yamibuy_wh.wh_storage_location wsl
            ON wsl.location_no=wsli.location_no AND wsl.warehouse_number=wsli.warehouse_number
        LEFT JOIN yamibuy_im.im_item ii ON ii.item_number=wsli.item_number
        WHERE wsli.warehouse_number='002' AND wit.quantity>0
          AND wsl.location_type IN (3,4) AND wsl.size_id NOT IN (13,33)
          AND IFNULL(ii.business_type,0)!=8
        GROUP BY wsli.item_number HAVING fc_qty>0 OR sfc_qty>0
    """)
    df_inv = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
conn.close()
df_inv["item_number"] = df_inv["item_number"].astype(str)
df_inv["fc_qty"]  = pd.to_numeric(df_inv["fc_qty"],  errors="coerce").fillna(0)
df_inv["sfc_qty"] = pd.to_numeric(df_inv["sfc_qty"], errors="coerce").fillna(0)
print(f"    → {len(df_inv):,} SKUs", flush=True)

# ── 2. Sales data (local CSV) ─────────────────────────────────
print("[ 2/4 ] Loading sales data (local CSV)...", flush=True)
csvs = sorted((ROOT / "v3_FC_segment" / "results").glob("segment_sku_detail_*.csv"), reverse=True)
if not csvs:
    sys.exit("ERROR: No segment CSV found")
df_sales = pd.read_csv(csvs[0])
df_sales["item_number"] = df_sales["item_number"].astype(str)
for col in ["active_order_days","order_lines_30d","avg_daily_orders"]:
    if col in df_sales.columns:
        df_sales[col] = pd.to_numeric(df_sales[col], errors="coerce").fillna(0)
print(f"    → {csvs[0].name} ({len(df_sales):,} rows)", flush=True)

# Unit volume
conn = mysql_conn()
with conn.cursor() as cur:
    cur.execute("""
        SELECT ii.item_number, COALESCE(wwg.volume, 0) AS unit_volume_in3
        FROM yamibuy_im.im_item ii
        LEFT JOIN yamibuy_master.xysc_wearhouse_goods wwg ON wwg.goods_id=ii.goods_id
    """)
    df_vol = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
conn.close()
df_vol["item_number"]    = df_vol["item_number"].astype(str)
df_vol["unit_volume_in3"]= pd.to_numeric(df_vol["unit_volume_in3"], errors="coerce").fillna(0)

# ── 3. Classify + compute D2S + triggers ──────────────────────
print("[ 3/4 ] Classifying SKUs and computing list triggers...", flush=True)
df = df_inv.merge(df_sales[["item_number","active_order_days","order_lines_30d",
                              "avg_daily_orders"]], on="item_number", how="left")
df = df.merge(df_vol, on="item_number", how="left")
df["avg_daily_orders"]  = df["avg_daily_orders"].fillna(0)
df["order_lines_30d"]   = df["order_lines_30d"].fillna(0)
df["active_order_days"] = df["active_order_days"].fillna(0)
df["unit_volume_in3"]   = df["unit_volume_in3"].fillna(0)

def classify(r):
    if r["active_order_days"] >= P["aod_top"] and r["order_lines_30d"] >= P["ol_top"]:
        return "TOP"
    if r["order_lines_30d"] < P["ol_tail"]:
        return "TAIL"
    return "MIDDLE"

df["segment"] = df.apply(classify, axis=1)
df["adl"]     = df["avg_daily_orders"]
df["fc_d2s"]  = np.where(df["adl"] > 0, (df["fc_qty"] / df["adl"]).round(1), np.nan)
df["needed"]  = np.where(df["adl"] > 0,
                         (np.ceil(df["adl"] * P["mid_target_d2s"]) - df["fc_qty"]).clip(lower=0),
                         0)

# ── TOP lists ─────────────────────────────────────────────────
df_top = df[(df["segment"] == "TOP") & (df["sfc_qty"] > 0)].copy()

# Tier A: fc_d2s < 3, sort by order_lines_30d DESC then avg_daily_orders DESC
df_top_A = df_top[df_top["fc_d2s"] < 3].copy()\
    .sort_values(["order_lines_30d","avg_daily_orders"], ascending=[False,False])
df_top_A["list"] = "TOP_FC_Less3D2S"
df_top_A["queue_rank"] = range(1, len(df_top_A)+1)

# Tier B: fc_d2s >= 3 (or NaN)
# Sort: EXP flag (sfc_exp < fc_exp → higher priority) → fc_d2s ASC → inv_age tiebreaker in lot detail
# EXP signal requires lot data; here we use a proxy: need_minimal_exp flag from SFC summary
# Full EXP comparison is shown in lot detail output
df_top_B = df_top[df_top["fc_d2s"].isna() | (df_top["fc_d2s"] >= 3)].copy()
df_top_B["list"] = "TOP_FC_Over3D2S"
# EXP sort applied after SFC summary is loaded (below); placeholder queue_rank assigned then

# ── MIDDLE lists ──────────────────────────────────────────────
# Need SFC lot info for EXP comparison — load summary first
# (full lot detail only for top 100 per list, loaded in step 4)
# For routing we need: fc_d2s, sfc_has_exp, sfc_exp < fc_exp, sfc_has_bin

# Pull SFC EXP and bin/stock summary per SKU (lightweight — no qty detail)
print("    Loading SFC EXP/location-type summary...", flush=True)
df_mid_candidates = df[(df["segment"] == "MIDDLE") & (df["sfc_qty"] > 0)].copy()
mid_items = df_mid_candidates["item_number"].tolist()

if mid_items:
    placeholders = ",".join(["%s"] * len(mid_items))
    conn = mysql_conn()
    with conn.cursor() as cur:
        # SFC lot summary: min exp_date on Bin locations only, has_bin flag
        cur.execute(f"""
            SELECT li.item_number,
                   MIN(CASE WHEN wsl.location_type=4 AND ib.qc_ExpiredDate>0
                            THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) END) AS sfc_bin_min_exp,
                   MAX(CASE WHEN wsl.location_type=4 THEN 1 ELSE 0 END)      AS sfc_has_bin,
                   MAX(IFNULL(iie.need_minimal_exp,0))                        AS need_minimal_exp
            FROM yamibuy_wh.wh_lot_inventory li
            JOIN yamibuy_wh.wh_storage_location wsl
                ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number
            LEFT JOIN yamibuy_wh.wh_lot_master lm ON lm.lot_id=li.lot_id
            LEFT JOIN yamibuy_wh.wh_inbound_batch ib ON ib.inbound_number=lm.source_number
            LEFT JOIN yamibuy_im.im_item_extend iie ON iie.item_number=li.item_number
            WHERE li.warehouse_number='002' AND li.quantity>0
              AND wsl.location_type IN (3,4)
              AND (li.location_no LIKE 'S%%' OR li.location_no LIKE 'DC%%')
              AND li.item_number IN ({placeholders})
            GROUP BY li.item_number
        """, mid_items)
        df_sfc_sum = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

        # FC min exp per SKU
        cur.execute(f"""
            SELECT li.item_number,
                   MIN(CASE WHEN ib.qc_ExpiredDate>0
                            THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) END) AS fc_min_exp
            FROM yamibuy_wh.wh_lot_inventory li
            JOIN yamibuy_wh.wh_storage_location wsl
                ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number
            LEFT JOIN yamibuy_wh.wh_lot_master lm ON lm.lot_id=li.lot_id
            LEFT JOIN yamibuy_wh.wh_inbound_batch ib ON ib.inbound_number=lm.source_number
            WHERE li.warehouse_number='002' AND li.quantity>0
              AND wsl.location_type IN (3,4)
              AND li.location_no NOT LIKE 'S%%'
              AND li.location_no NOT LIKE 'DC%%'
              AND li.item_number IN ({placeholders})
            GROUP BY li.item_number
        """, mid_items)
        df_fc_exp = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    conn.close()

    df_sfc_sum["item_number"]      = df_sfc_sum["item_number"].astype(str)
    df_sfc_sum["sfc_bin_min_exp"]  = pd.to_datetime(df_sfc_sum["sfc_bin_min_exp"], errors="coerce")
    df_sfc_sum["sfc_has_bin"]      = pd.to_numeric(df_sfc_sum["sfc_has_bin"],      errors="coerce").fillna(0)
    df_sfc_sum["need_minimal_exp"] = pd.to_numeric(df_sfc_sum["need_minimal_exp"], errors="coerce").fillna(0)
    df_fc_exp["item_number"]  = df_fc_exp["item_number"].astype(str)
    df_fc_exp["fc_min_exp"]   = pd.to_datetime(df_fc_exp["fc_min_exp"],  errors="coerce")

    df_mid_candidates = df_mid_candidates.merge(df_sfc_sum, on="item_number", how="left")
    df_mid_candidates = df_mid_candidates.merge(df_fc_exp,  on="item_number", how="left")
    df_mid_candidates["exp_earlier_in_sfc"] = (          # sfc_bin_min_exp <= fc_min_exp → ZonePick
        (df_mid_candidates["need_minimal_exp"] == 1) &
        df_mid_candidates["sfc_bin_min_exp"].notna() &
        df_mid_candidates["fc_min_exp"].notna() &
        (df_mid_candidates["sfc_bin_min_exp"] <= df_mid_candidates["fc_min_exp"])
    )
    df_mid_candidates["exp_later_in_sfc"] = (            # sfc_bin_min_exp > fc_min_exp → DateSwitch
        (df_mid_candidates["need_minimal_exp"] == 1) &
        df_mid_candidates["sfc_bin_min_exp"].notna() &
        df_mid_candidates["fc_min_exp"].notna() &
        (df_mid_candidates["sfc_bin_min_exp"] > df_mid_candidates["fc_min_exp"])
    )

    # Tier B: same EXP flag using sfc_bin_min_exp
    df_top_B = df_top_B.merge(df_sfc_sum[["item_number","sfc_bin_min_exp","need_minimal_exp"]],
                               on="item_number", how="left")
    df_top_B = df_top_B.merge(df_fc_exp, on="item_number", how="left")
    df_top_B["exp_earlier_in_sfc"] = (
        (df_top_B["need_minimal_exp"] == 1) &
        df_top_B["sfc_bin_min_exp"].notna() &
        df_top_B["fc_min_exp"].notna() &
        (df_top_B["sfc_bin_min_exp"] <= df_top_B["fc_min_exp"])
    )
    # EXP flag: 1 = SFC has earlier exp (higher priority) → sort DESC
    df_top_B["_exp_sort"] = df_top_B["exp_earlier_in_sfc"].astype(int)
    df_top_B = df_top_B.sort_values(
        ["_exp_sort", "fc_d2s"], ascending=[False, True], na_position="last"
    ).drop(columns=["_exp_sort"])
    df_top_B["queue_rank"] = range(1, len(df_top_B)+1)
else:
    df_mid_candidates["sfc_has_bin"]        = 0
    df_mid_candidates["exp_earlier_in_sfc"] = False

# Route MIDDLE SKUs
def route_middle(r):
    d2s = r["fc_d2s"]
    if pd.isna(d2s):
        return "Keep"
    if d2s < 1:
        return "MIDDLE_Replen_Emergency"
    if r["sfc_has_bin"] == 1:
        if r.get("exp_earlier_in_sfc", False):   # sfc_min_exp <= fc_min_exp
            return "MIDDLE_ZonePick"
        if r.get("exp_later_in_sfc", False):     # sfc_min_exp > fc_min_exp
            return "MIDDLE_DateSwitch"
    if d2s < P["mid_emergency_d2s"]:
        return "MIDDLE_Replen_Routine"
    return "Keep"

df_mid_candidates["list"] = df_mid_candidates.apply(route_middle, axis=1)

# Sort within each list: fc_d2s ASC → batch_volume DESC → avg_daily_orders DESC
df_mid_candidates["batch_volume"] = df_mid_candidates["needed"] * df_mid_candidates["unit_volume_in3"]

def sort_middle_list(df_list):
    return df_list.sort_values(
        ["fc_d2s","batch_volume","avg_daily_orders"],
        ascending=[True, False, False]
    )

df_mid1 = sort_middle_list(df_mid_candidates[df_mid_candidates["list"]=="MIDDLE_Replen_Emergency"].copy())
df_mid2 = sort_middle_list(df_mid_candidates[df_mid_candidates["list"]=="MIDDLE_Replen_Routine"].copy())
df_mid3 = sort_middle_list(df_mid_candidates[df_mid_candidates["list"]=="MIDDLE_ZonePick"].copy())
df_mid4 = sort_middle_list(df_mid_candidates[df_mid_candidates["list"]=="MIDDLE_DateSwitch"].copy())

for df_l in [df_mid1, df_mid2, df_mid3, df_mid4]:
    df_l["queue_rank"] = range(1, len(df_l)+1)

print(f"    TOP  FC < 3 D2S  : {len(df_top_A):,} SKUs", flush=True)
print(f"    TOP  FC >= 3 D2S : {len(df_top_B):,} SKUs", flush=True)
print(f"    MID  List 1 (Emergency)  : {len(df_mid1):,} SKUs", flush=True)
print(f"    MID  List 2 (Routine)    : {len(df_mid2):,} SKUs", flush=True)
print(f"    MID  List 3 (ZonePick)   : {len(df_mid3):,} SKUs", flush=True)
print(f"    MID  List 4 (DateSwitch) : {len(df_mid4):,} SKUs", flush=True)

# ── Summary ───────────────────────────────────────────────────
def make_summary_row(label, df_sku):
    if df_sku.empty:
        return {"list": label, "triggered_skus": 0, "total_sfc_qty": 0,
                "fc_d2s_median": None, "fc_d2s_min": None, "fc_d2s_max": None,
                "total_needed_qty": 0, "total_batch_vol_in3": 0}
    d2s = df_sku["fc_d2s"].dropna()
    return {
        "list":              label,
        "triggered_skus":    len(df_sku),
        "total_sfc_qty":     int(df_sku["sfc_qty"].sum()),
        "fc_d2s_min":        round(d2s.min(),    1) if len(d2s) else None,
        "fc_d2s_median":     round(d2s.median(), 1) if len(d2s) else None,
        "fc_d2s_max":        round(d2s.max(),    1) if len(d2s) else None,
        "total_needed_qty":  int(df_sku["needed"].sum()) if "needed" in df_sku else 0,
        "total_batch_vol_in3": round(df_sku["batch_volume"].sum(), 0) if "batch_volume" in df_sku else 0,
    }

summary = pd.DataFrame([
    make_summary_row("TOP_FC_Less3D2S",         df_top_A),
    make_summary_row("TOP_FC_Over3D2S",         df_top_B),
    make_summary_row("MIDDLE_Replen_Emergency", df_mid1),
    make_summary_row("MIDDLE_Replen_Routine",   df_mid2),
    make_summary_row("MIDDLE_ZonePick",         df_mid3),
    make_summary_row("MIDDLE_DateSwitch",       df_mid4),
])
summary.to_csv(OUT_DIR / "summary.csv", index=False)

# ── 4. SFC lot detail for top 100 per list ────────────────────
print("[ 4/4 ] Loading SFC lot detail for top 100 per list...", flush=True)

all_top100 = {
    "TOP_FC_Less3D2S":         df_top_A.head(TOP_N)["item_number"].tolist(),
    "TOP_FC_Over3D2S":         df_top_B.head(TOP_N)["item_number"].tolist(),
    "MIDDLE_Replen_Emergency": df_mid1.head(TOP_N)["item_number"].tolist(),
    "MIDDLE_Replen_Routine":   df_mid2.head(TOP_N)["item_number"].tolist(),
    "MIDDLE_ZonePick":         df_mid3.head(TOP_N)["item_number"].tolist(),
    "MIDDLE_DateSwitch":       df_mid4.head(TOP_N)["item_number"].tolist(),
}
all_skus = list(set(s for skus in all_top100.values() for s in skus))

if all_skus:
    placeholders = ",".join(["%s"] * len(all_skus))
    conn = mysql_conn()
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT li.item_number, li.location_no,
                   wsl.location_type                                      AS loc_type,
                   li.quantity                                            AS qty,
                   li.lot_id,
                   CASE WHEN li.lot_id=1 THEN 1 ELSE 0 END               AS is_legacy,
                   CASE WHEN ib.qc_ExpiredDate>0
                        THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) END   AS sfc_exp_date,
                   MAX(IFNULL(iie.need_minimal_exp,0))                    AS need_minimal_exp,
                   COALESCE(NULLIF(iie.piece_pack,0),1)                   AS case_qty,
                   DATEDIFF(CURDATE(), DATE(FROM_UNIXTIME(
                       CASE WHEN li.lot_id=1 THEN ib.in_dtm
                            ELSE lm.receive_time END)))                   AS inv_age
            FROM yamibuy_wh.wh_lot_inventory li
            JOIN yamibuy_wh.wh_storage_location wsl
                ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number
            LEFT JOIN yamibuy_wh.wh_lot_master lm ON lm.lot_id=li.lot_id
            LEFT JOIN yamibuy_wh.wh_inbound_batch ib ON ib.inbound_number=lm.source_number
            LEFT JOIN yamibuy_im.im_item_extend iie ON iie.item_number=li.item_number
            WHERE li.warehouse_number='002' AND li.quantity>0
              AND wsl.location_type IN (3,4)
              AND (li.location_no LIKE 'S%%' OR li.location_no LIKE 'DC%%')
              AND li.item_number IN ({placeholders})
            GROUP BY li.item_number, li.location_no, wsl.location_type,
                     li.quantity, li.lot_id, ib.qc_ExpiredDate,
                     iie.piece_pack, lm.receive_time, ib.in_dtm
            HAVING qty>={P['min_loc_qty']}
        """, all_skus)
        df_lot = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    conn.close()

    df_lot["item_number"]      = df_lot["item_number"].astype(str)
    df_lot["qty"]              = pd.to_numeric(df_lot["qty"],              errors="coerce").fillna(0)
    df_lot["need_minimal_exp"] = pd.to_numeric(df_lot["need_minimal_exp"], errors="coerce").fillna(0)
    df_lot["case_qty"]         = pd.to_numeric(df_lot["case_qty"],         errors="coerce").fillna(1).clip(lower=1)
    df_lot["inv_age"]          = pd.to_numeric(df_lot["inv_age"],          errors="coerce").fillna(0)
    df_lot["sfc_exp_date"]     = pd.to_datetime(df_lot["sfc_exp_date"],    errors="coerce")
    df_lot["loc_type_label"]   = df_lot["loc_type"].map({3:"Stock",4:"Bin"}).fillna("Other")
    print(f"    → {len(df_lot):,} SFC lot rows for {len(all_skus)} SKUs", flush=True)

    DETAIL_COLS = ["queue_rank","item_number","location_no","loc_type_label",
                   "qty","case_qty","sfc_exp_date","sfc_bin_min_exp","fc_min_exp",
                   "need_minimal_exp","inv_age","is_legacy",
                   "fc_qty","sfc_qty","fc_d2s","adl","needed"]

    file_map = {
        "TOP_FC_Less3D2S":         ("top_fc_less3d2s_top100.csv",      df_top_A),
        "TOP_FC_Over3D2S":         ("top_fc_over3d2s_top100.csv",      df_top_B),
        "MIDDLE_Replen_Emergency": ("middle_emergency_top100.csv",     df_mid1),
        "MIDDLE_Replen_Routine":   ("middle_routine_top100.csv",       df_mid2),
        "MIDDLE_ZonePick":         ("middle_zonepick_top100.csv",      df_mid3),
        "MIDDLE_DateSwitch":       ("middle_dateswitch_top100.csv",    df_mid4),
    }

    for list_name, (filename, df_sku) in file_map.items():
        top_items = all_top100[list_name]
        df_sku_top = df_sku[df_sku["item_number"].isin(top_items)].copy()
        df_detail  = df_lot[df_lot["item_number"].isin(top_items)].copy()
        df_sku_sku_cols = ["item_number","queue_rank","fc_qty","sfc_qty","fc_d2s","adl","needed"]
        for extra in ["sfc_bin_min_exp","fc_min_exp","batch_volume"]:
            if extra in df_sku_top.columns:
                df_sku_sku_cols.append(extra)
        df_out = df_detail.merge(df_sku_top[df_sku_sku_cols], on="item_number", how="left")
        out_cols = [c for c in DETAIL_COLS if c in df_out.columns]
        df_out[out_cols].sort_values(["queue_rank","location_no"])\
            .to_csv(OUT_DIR / filename, index=False)

# ── Print results ─────────────────────────────────────────────
print("\n─── Summary ─────────────────────────────────────────────────")
print(summary.to_string(index=False))
print(f"\nFiles saved to: {OUT_DIR}")
