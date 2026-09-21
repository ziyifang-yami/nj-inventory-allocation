"""
V3 FC Segment — Ideal State Allocation (no daily cap)
=======================================================
同 run_allocation.py，但移除 MIDDLE daily cap，
用于计算"理想状态"全量分配结果。

Output: results/ideal_allocation_v3_YYYYMMDD_HHMM.csv
"""

import os, pymysql, pandas as pd, numpy as np
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime, date

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

OUT_DIR = Path(__file__).parent / "results"

# ── Parameters ────────────────────────────────────────────────
P = dict(
    top_trigger_d2s   = 28,
    top_target_d2s    = 28,
    mid_routine_d2s   = 28,
    mid_emergency_d2s = 14,
    mid_target_d2s    = 28,
    mid_evict_d2s     = 30,
    # mid_daily_cap removed — ideal state, no cap
    tolerance_in      = 0.15,
    tolerance_out     = 0.15,
    min_loc_qty       = 5,
)

def connect():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
    )

IN3_TO_CBM = 1.0

# ── Load base data ─────────────────────────────────────────────
print("Loading segment SKU detail...", flush=True)
df_seg = pd.read_csv(OUT_DIR / "segment_sku_detail_20260917_2247.csv")
df_seg["item_number"] = df_seg["item_number"].astype(str)

print("Loading zone inventory...", flush=True)
df_zone = pd.read_csv(OUT_DIR / "zone_inventory.csv")
df_zone["item_number"] = df_zone["item_number"].astype(str)
df_zone["qty"] = pd.to_numeric(df_zone["qty"], errors="coerce").fillna(0)
df_zone["unit_volume_cbm"] = pd.to_numeric(df_zone["unit_volume_cbm"], errors="coerce").fillna(0) * IN3_TO_CBM

inv_agg = df_zone.groupby(["item_number", "zone"])["qty"].sum().unstack(fill_value=0).reset_index()
inv_agg.columns.name = None
if "FC"  not in inv_agg.columns: inv_agg["FC"]  = 0
if "SFC" not in inv_agg.columns: inv_agg["SFC"] = 0
inv_agg = inv_agg.rename(columns={"FC": "fc_qty_live", "SFC": "sfc_qty_live"})

df = df_seg.merge(inv_agg, on="item_number", how="left")
df["fc_qty_live"]  = df["fc_qty_live"].fillna(0)
df["sfc_qty_live"] = df["sfc_qty_live"].fillna(0)
df["avg_daily_orders"] = pd.to_numeric(df["avg_daily_orders"], errors="coerce").fillna(0)
df["unit_volume_cbm"]  = pd.to_numeric(df["unit_volume_cbm"],  errors="coerce").fillna(0) * IN3_TO_CBM

df["fc_d2s"] = np.where(
    df["avg_daily_orders"] > 0,
    (df["fc_qty_live"] / df["avg_daily_orders"]).round(1),
    np.nan
)
df["seg"] = df["segment"]

# ── Lot data ──────────────────────────────────────────────────
print("Loading lot data from MySQL...", flush=True)
conn = connect()
with conn.cursor() as cur:
    cur.execute("""
        SELECT
            li.item_number,
            CASE WHEN wsl.zone_id = 2 THEN 'SFC' ELSE 'FC' END AS zone,
            li.location_no,
            li.quantity,
            li.lot_id,
            CASE WHEN li.lot_id = 1 THEN 1 ELSE 0 END AS is_legacy,
            DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) AS exp_date,
            DATE(FROM_UNIXTIME(ib.in_dtm))         AS recv_date
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl
          ON wsl.location_no = li.location_no
         AND wsl.warehouse_number = li.warehouse_number
        LEFT JOIN yamibuy_wh.wh_lot_master lm ON li.lot_id = lm.lot_id
        LEFT JOIN yamibuy_wh.wh_inbound_batch ib
          ON lm.source_number = ib.inbound_number
        WHERE li.warehouse_number = '002'
          AND li.quantity > 0
          AND wsl.location_type IN (3, 4)
          AND (li.item_number LIKE '1%%' OR li.item_number LIKE '5%%')
    """)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
conn.close()

df_lot = pd.DataFrame(rows, columns=cols)
df_lot["item_number"] = df_lot["item_number"].astype(str)
df_lot["quantity"]    = pd.to_numeric(df_lot["quantity"], errors="coerce").fillna(0)
df_lot["exp_date"]    = pd.to_datetime(df_lot["exp_date"],  errors="coerce")
df_lot["recv_date"]   = pd.to_datetime(df_lot["recv_date"], errors="coerce")
print(f"  → {len(df_lot):,} lot rows", flush=True)

def lot_summary(lot_df, zone):
    sub = lot_df[lot_df["zone"] == zone]
    out = sub.groupby("item_number").agg(
        has_exp    = ("exp_date",    lambda x: x.notna().any()),
        min_exp    = ("exp_date",    "min"),
        min_recv   = ("recv_date",   "min"),
        has_legacy = ("is_legacy",   "max"),
        loc_count  = ("location_no", "nunique"),
    ).reset_index()
    out.columns = [f"{c}_{zone.lower()}" if c != "item_number" else c for c in out.columns]
    return out

df = df.merge(lot_summary(df_lot, "FC"),  on="item_number", how="left")
df = df.merge(lot_summary(df_lot, "SFC"), on="item_number", how="left")

# ── Allocation Engine (no cap) ────────────────────────────────
today   = pd.Timestamp(date.today())
records = []

for _, row in df.iterrows():
    sku          = row["item_number"]
    seg          = str(row.get("seg", "TAIL"))
    fc           = float(row["fc_qty_live"])
    sfc          = float(row["sfc_qty_live"])
    adl          = float(row["avg_daily_orders"])
    vol          = float(row["unit_volume_cbm"])
    fc_d2s       = row["fc_d2s"]

    action       = "KEEP"
    direction    = ""
    transfer_qty = 0.0
    reason       = ""
    priority     = 999

    target_qty = round(adl * P["mid_target_d2s"], 0) if adl > 0 else 0.0

    # ── TOP ───────────────────────────────────────────────────
    if seg == "TOP":
        if adl > 0 and (pd.isna(fc_d2s) or fc_d2s < P["top_trigger_d2s"]):
            needed = max(0.0, target_qty - fc)
            avail  = sfc
            if needed > 0 and avail > 0:
                xfer         = min(needed, avail)
                action       = "DC_TO_FC"
                direction    = "SFC→FC"
                transfer_qty = round(xfer, 0)
                reason       = f"FC D2S={fc_d2s:.1f} < {P['top_trigger_d2s']}, need {needed:.0f}"

                leg_fc   = row.get("has_legacy_fc",  0)
                leg_sfc  = row.get("has_legacy_sfc", 0)
                has_exp_fc  = row.get("has_exp_fc",  False)
                has_exp_sfc = row.get("has_exp_sfc", False)
                min_exp_fc  = row.get("min_exp_fc")
                min_exp_sfc = row.get("min_exp_sfc")
                min_recv_fc = row.get("min_recv_fc")
                min_recv_sfc= row.get("min_recv_sfc")

                exp_trigger = False
                if leg_sfc and not leg_fc: exp_trigger = True
                elif has_exp_sfc and has_exp_fc:
                    if pd.notna(min_exp_sfc) and pd.notna(min_exp_fc):
                        exp_trigger = min_exp_sfc < min_exp_fc

                age_trigger = False
                if leg_sfc and not leg_fc: age_trigger = True
                elif pd.notna(min_recv_sfc) and pd.notna(min_recv_fc):
                    age_trigger = min_recv_sfc < min_recv_fc

                if exp_trigger:   priority = 1
                elif age_trigger: priority = 2
                else:             priority = 3 + (P["top_trigger_d2s"] - (fc_d2s if not pd.isna(fc_d2s) else 0)) / 100

    # ── MIDDLE ────────────────────────────────────────────────
    elif seg == "MIDDLE":
        if adl > 0:
            d2s = fc_d2s if not pd.isna(fc_d2s) else 0.0

            if d2s < P["mid_routine_d2s"] and sfc > 0:
                needed = max(0.0, target_qty - fc)
                if needed > 0:
                    xfer         = min(needed, sfc)
                    action       = "DC_TO_FC"
                    direction    = "SFC→FC"
                    transfer_qty = round(xfer, 0)
                    emerg        = d2s < P["mid_emergency_d2s"]
                    reason       = f"FC D2S={d2s:.1f} {'[EMERGENCY]' if emerg else '[ROUTINE]'}"
                    priority     = d2s * 1000 - needed * vol

            elif d2s > P["mid_evict_d2s"]:
                keep_qty = round(adl * P["mid_evict_d2s"], 0)
                excess   = fc - keep_qty
                if excess > P["min_loc_qty"]:
                    action       = "FC_TO_DC"
                    direction    = "FC→SFC"
                    transfer_qty = round(excess, 0)
                    reason       = f"FC D2S={d2s:.1f} > {P['mid_evict_d2s']}, evict {excess:.0f}"
                    priority     = 500 + d2s

    # ── TAIL ─────────────────────────────────────────────────
    elif seg == "TAIL":
        if fc > 0:
            action       = "FC_TO_DC"
            direction    = "FC→SFC"
            transfer_qty = fc
            reason       = "TAIL: clear FC"
            priority     = 100

    records.append({
        "item_number":      sku,
        "seg":              seg,
        "biz_type":         "自营" if sku[0] == "1" else "FBY",
        "fc_qty":           fc,
        "sfc_qty":          sfc,
        "fc_d2s":           round(fc_d2s, 1) if not pd.isna(fc_d2s) else None,
        "avg_daily":        round(adl, 2),
        "unit_vol_in3":     vol,
        "action":           action,
        "direction":        direction,
        "transfer_qty":     transfer_qty,
        "transfer_vol_in3": round(transfer_qty * vol, 1),
        "target_fc_qty":    target_qty,
        "priority_score":   round(priority, 3),
        "reason":           reason,
        "fc_has_exp":       bool(row.get("has_exp_fc",  False)),
        "sfc_has_exp":      bool(row.get("has_exp_sfc", False)),
        "fc_min_exp":       str(row.get("min_exp_fc",  ""))[:10] if pd.notna(row.get("min_exp_fc")) else "",
        "sfc_min_exp":      str(row.get("min_exp_sfc", ""))[:10] if pd.notna(row.get("min_exp_sfc")) else "",
    })

# ── NO daily cap applied ───────────────────────────────────────
result = pd.DataFrame(records).sort_values(["seg", "priority_score"]).reset_index(drop=True)

# ── Summary ───────────────────────────────────────────────────
print("\n" + "=" * 65)
print("IDEAL STATE ALLOCATION SUMMARY (no daily cap)")
print("=" * 65)
for seg in ["TOP", "MIDDLE", "TAIL"]:
    sub = result[result["seg"] == seg]
    for act in ["DC_TO_FC", "FC_TO_DC", "KEEP"]:
        s = sub[sub["action"] == act]
        if len(s) == 0: continue
        print(f"  [{seg}] {act:10s} → {len(s):>5,} SKUs  "
              f"qty={s['transfer_qty'].sum():>10,.0f}  "
              f"vol={s['transfer_vol_in3'].sum():>12,.0f} in³")

print(f"\n  Total DC→FC: {result[result['action']=='DC_TO_FC']['transfer_qty'].sum():>10,.0f} units")
print(f"  Total FC→DC: {result[result['action']=='FC_TO_DC']['transfer_qty'].sum():>10,.0f} units")

# ── Save ──────────────────────────────────────────────────────
ts = datetime.now().strftime("%Y%m%d_%H%M")
out_path = OUT_DIR / f"ideal_allocation_v3_{ts}.csv"
result.to_csv(out_path, index=False)
print(f"\n✅ Saved: {out_path}  ({len(result):,} rows)")
