"""
FC → SFC Lists Diagnostic Report (快速版)
==========================================
Summary: 全量 SKU 计算
明细:    每个 list 只输出优先级前 100 个 SKU 的库位明细

输出：
  results/fc_to_sfc_report_<date>/
    ├── summary.csv                  — 全量汇总
    ├── middle_evict_top100.csv      — MIDDLE 超储前 100 SKU 明细
    └── tail_clear_top100.csv        — TAIL 清仓前 100 SKU 明细

运行：
  cd c:\\Project-Z\\NJ_Inventory_Allocation
  python v4_DC_allocation/explore/fc_to_sfc_lists_report.py
"""

import os, sys, math, pymysql, pandas as pd, numpy as np
from pathlib import Path
from dotenv import load_dotenv
from datetime import date, datetime

ROOT = Path(__file__).parent.parent.parent
load_dotenv(dotenv_path=ROOT / ".env")

TODAY = date.today()
OUT_DIR = Path(__file__).parent.parent / "results" / f"fc_to_sfc_report_{TODAY:%Y%m%d}"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TOP_N = 100   # 每个 list 取前 N 个 SKU

P = dict(
    aod_top       = 21,
    ol_top        = 30,
    ol_tail       = 4,
    mid_evict_d2s = 30,
    mid_keep_d2s  = 30,
    min_loc_qty   = 5,
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

# ── 2. Sales data (fallback CSV only, no Athena) ──────────────
print("[ 2/4 ] Loading sales data (local CSV)...", flush=True)
csvs = sorted((ROOT / "v3_FC_segment" / "results").glob("segment_sku_detail_*.csv"), reverse=True)
if not csvs:
    sys.exit("ERROR: No segment CSV found in v3_FC_segment/results/")
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
        SELECT ii.item_number, COALESCE(wwg.volume, 0) AS unit_volume_cbm
        FROM yamibuy_im.im_item ii
        LEFT JOIN yamibuy_master.xysc_wearhouse_goods wwg ON wwg.goods_id=ii.goods_id
    """)
    df_vol = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
conn.close()
df_vol["item_number"]     = df_vol["item_number"].astype(str)
df_vol["unit_volume_cbm"] = pd.to_numeric(df_vol["unit_volume_cbm"], errors="coerce").fillna(0)

# ── 3. Classify all SKUs, compute triggers ────────────────────
print("[ 3/4 ] Classifying and computing triggers...", flush=True)
df = df_inv.merge(df_sales[["item_number","active_order_days","order_lines_30d",
                              "avg_daily_orders"]], on="item_number", how="left")
df = df.merge(df_vol, on="item_number", how="left")
df["avg_daily_orders"]  = df["avg_daily_orders"].fillna(0)
df["order_lines_30d"]   = df["order_lines_30d"].fillna(0)
df["active_order_days"] = df["active_order_days"].fillna(0)
df["unit_volume_cbm"]   = df["unit_volume_cbm"].fillna(0)

def classify(r):
    if r["active_order_days"] >= P["aod_top"] and r["order_lines_30d"] >= P["ol_top"]:
        return "TOP"
    if r["order_lines_30d"] < P["ol_tail"]:
        return "TAIL"
    return "MIDDLE"

df["segment"] = df.apply(classify, axis=1)
df["adl"]     = df["avg_daily_orders"]
df["fc_d2s"]  = np.where(df["adl"] > 0, (df["fc_qty"] / df["adl"]).round(1), np.nan)

# MIDDLE trigger
df_mid_all = df[(df["segment"] == "MIDDLE") & (df["fc_d2s"] > P["mid_evict_d2s"])].copy()
df_mid_all["keep_qty"]   = np.ceil(df_mid_all["adl"] * P["mid_keep_d2s"])
df_mid_all["excess_qty"] = (df_mid_all["fc_qty"] - df_mid_all["keep_qty"]).clip(lower=0)
df_mid_all["excess_vol"] = (df_mid_all["excess_qty"] * df_mid_all["unit_volume_cbm"]).round(4)
df_mid_all = df_mid_all.sort_values(["excess_vol","avg_daily_orders"], ascending=[False,False])
df_mid_all["queue_rank"] = range(1, len(df_mid_all) + 1)

# TAIL trigger
df_tail_all = df[(df["segment"] == "TAIL") & (df["fc_qty"] > 0)].copy()
df_tail_all = df_tail_all.sort_values("fc_qty", ascending=False)
df_tail_all["queue_rank"] = range(1, len(df_tail_all) + 1)

print(f"    MIDDLE triggered: {len(df_mid_all):,} SKUs", flush=True)
print(f"    TAIL   triggered: {len(df_tail_all):,} SKUs", flush=True)

# ── Summary (full population, no lot data needed) ─────────────
seg_counts = df["segment"].value_counts()
summary = pd.DataFrame([
    {
        "list":                 "FC→SFC MIDDLE (evict, fc_d2s>30)",
        "triggered_skus":       len(df_mid_all),
        "total_fc_qty":         int(df_mid_all["fc_qty"].sum()),
        "total_excess_qty":     int(df_mid_all["excess_qty"].sum()),
        "total_excess_vol_cbm": round(df_mid_all["excess_vol"].sum(), 2),
        "fc_d2s_median":        round(df_mid_all["fc_d2s"].median(), 1) if len(df_mid_all) else None,
        "fc_d2s_max":           round(df_mid_all["fc_d2s"].max(), 1)    if len(df_mid_all) else None,
        "note": f"Total MIDDLE SKUs: {seg_counts.get('MIDDLE',0):,}",
    },
    {
        "list":                 "FC→SFC TAIL (clear, fc_qty>0)",
        "triggered_skus":       len(df_tail_all),
        "total_fc_qty":         int(df_tail_all["fc_qty"].sum()),
        "total_excess_qty":     int(df_tail_all["fc_qty"].sum()),
        "total_excess_vol_cbm": round((df_tail_all["fc_qty"] * df_tail_all["unit_volume_cbm"]).sum(), 2),
        "fc_d2s_median":        None,
        "fc_d2s_max":           None,
        "note": f"Total TAIL SKUs: {seg_counts.get('TAIL',0):,}",
    },
])
summary.to_csv(OUT_DIR / "summary.csv", index=False)

# ── 4. Lot detail — top 100 SKUs each list ────────────────────
print("[ 4/4 ] Loading lot detail for top 100 SKUs per list...", flush=True)

mid_top100  = df_mid_all.head(TOP_N)["item_number"].tolist()
tail_top100 = df_tail_all.head(TOP_N)["item_number"].tolist()
all_skus    = list(set(mid_top100 + tail_top100))

if all_skus:
    placeholders = ",".join(["%s"] * len(all_skus))
    conn = mysql_conn()
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT
                li.item_number,
                li.location_no,
                wsl.location_type                                     AS loc_type,
                li.quantity                                           AS qty,
                li.lot_id,
                CASE WHEN li.lot_id=1 THEN 1 ELSE 0 END              AS is_legacy,
                CASE WHEN ib.qc_ExpiredDate>0
                     THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) END  AS exp_date,
                MAX(IFNULL(iie.need_minimal_exp,0))                   AS need_minimal_exp
            FROM yamibuy_wh.wh_lot_inventory li
            JOIN yamibuy_wh.wh_storage_location wsl
                ON wsl.location_no=li.location_no AND wsl.warehouse_number=li.warehouse_number
            LEFT JOIN yamibuy_wh.wh_lot_master lm ON lm.lot_id=li.lot_id
            LEFT JOIN yamibuy_wh.wh_inbound_batch ib ON ib.inbound_number=lm.source_number
            LEFT JOIN yamibuy_im.im_item_extend iie ON iie.item_number=li.item_number
            WHERE li.warehouse_number='002' AND li.quantity>0
              AND wsl.location_type IN (3,4)
              AND li.location_no NOT LIKE 'S%%'
              AND li.location_no NOT LIKE 'DC%%'
              AND li.item_number IN ({placeholders})
            GROUP BY li.item_number, li.location_no, wsl.location_type,
                     li.quantity, li.lot_id, ib.qc_ExpiredDate
            HAVING qty>={P['min_loc_qty']}
        """, all_skus)
        df_lot = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    conn.close()

    df_lot["item_number"]     = df_lot["item_number"].astype(str)
    df_lot["qty"]             = pd.to_numeric(df_lot["qty"],              errors="coerce").fillna(0)
    df_lot["need_minimal_exp"]= pd.to_numeric(df_lot["need_minimal_exp"], errors="coerce").fillna(0)
    df_lot["exp_date"]        = pd.to_datetime(df_lot["exp_date"],        errors="coerce")
    df_lot["loc_type_label"]  = df_lot["loc_type"].map({3:"Stock",4:"Bin"}).fillna("Other")
    print(f"    → {len(df_lot):,} lot rows for {len(all_skus)} SKUs", flush=True)

    # ── MIDDLE top100 detail ──────────────────────────────────
    df_mid_lot = df_lot[df_lot["item_number"].isin(mid_top100)].copy()
    df_mid_lot = df_mid_lot.merge(
        df_mid_all[["item_number","adl","keep_qty","excess_qty","excess_vol",
                    "fc_d2s","fc_qty","sfc_qty","queue_rank","unit_volume_cbm"]],
        on="item_number", how="left"
    )
    # Exclude single-location SKUs; determine keep vs transfer
    def assign_transfer(grp):
        if len(grp) == 1:
            return grp.assign(_transfer=False)
        has_exp = grp["need_minimal_exp"].max() == 1
        if has_exp:
            grp = grp.sort_values("exp_date", ascending=True)
        else:
            grp["_t"] = grp["loc_type"].map({4:0,3:1}).fillna(2)
            grp = grp.sort_values(["_t","qty"], ascending=[True,False])
        grp = grp.reset_index(drop=True)
        cum = grp["qty"].cumsum()
        keep_target = grp["keep_qty"].iloc[0]
        first_over  = (cum >= keep_target).idxmax()
        grp["_transfer"] = grp.index > first_over
        return grp

    df_mid_lot = df_mid_lot.groupby("item_number", group_keys=False).apply(assign_transfer)
    df_mid_detail = df_mid_lot[df_mid_lot["_transfer"]].copy()
    df_mid_detail["qty_to_transfer"] = df_mid_detail["qty"]
    df_mid_detail["transfer_vol"]    = (df_mid_detail["qty_to_transfer"] * df_mid_detail["unit_volume_cbm"]).round(4)
    df_mid_detail["rule"] = np.where(df_mid_detail["need_minimal_exp"]==1,
                                     "MIDDLE_Evict_EXP", "MIDDLE_Evict_BinStock")

    mid_out_cols = ["queue_rank","item_number","location_no","loc_type_label",
                    "qty","qty_to_transfer","transfer_vol",
                    "fc_qty","sfc_qty","fc_d2s","adl","keep_qty","excess_qty","excess_vol",
                    "exp_date","need_minimal_exp","is_legacy","rule"]
    df_mid_detail[[c for c in mid_out_cols if c in df_mid_detail.columns]]\
        .sort_values(["queue_rank","location_no"])\
        .to_csv(OUT_DIR / "middle_evict_top100.csv", index=False)

    # ── TAIL top100 detail ────────────────────────────────────
    df_tail_lot = df_lot[df_lot["item_number"].isin(tail_top100)].copy()
    df_tail_lot = df_tail_lot.merge(
        df_tail_all[["item_number","fc_qty","sfc_qty","adl","queue_rank","unit_volume_cbm"]],
        on="item_number", how="left"
    )
    df_tail_lot["qty_to_transfer"] = df_tail_lot["qty"]
    df_tail_lot["transfer_vol"]    = (df_tail_lot["qty_to_transfer"] * df_tail_lot["unit_volume_cbm"]).round(4)
    df_tail_lot["rule"]            = "TAIL_FullClear"

    tail_out_cols = ["queue_rank","item_number","location_no","loc_type_label",
                     "qty","qty_to_transfer","transfer_vol",
                     "fc_qty","sfc_qty","adl",
                     "exp_date","need_minimal_exp","is_legacy","rule"]
    df_tail_lot[[c for c in tail_out_cols if c in df_tail_lot.columns]]\
        .sort_values(["queue_rank","location_no"])\
        .to_csv(OUT_DIR / "tail_clear_top100.csv", index=False)

# ── Print results ─────────────────────────────────────────────
print("\n─── Summary ─────────────────────────────────────────────────")
print(summary[["list","triggered_skus","total_fc_qty","total_excess_qty",
               "total_excess_vol_cbm","fc_d2s_median","fc_d2s_max","note"]].to_string(index=False))
print(f"\nFiles saved to: {OUT_DIR}")
