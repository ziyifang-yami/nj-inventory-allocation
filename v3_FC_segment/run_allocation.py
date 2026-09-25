"""
V3 FC Segment — Allocation Engine
===================================
用当前数据（segment_sku_detail + zone_inventory）跑分配建议。

三个 Tier 逻辑：
  TOP    : DC→FC 补货（FC D2S < 28 触发，补到 28D）
  MIDDLE : DC→FC 补回（FC D2S < 28 routine / < 14 emergency）
           FC→DC 迁出（FC D2S > 30，保留 30D）
           每日容量 ≤ 500 SKU
  TAIL   : FC→DC 全清

库位选择（FC→DC 迁出）：
  1) 无过期日 + 多库位 → 最接近 transfer_qty 的库位组合（tolerance=15%，宁少勿多）
  2) 无过期日 + 单库位 → 保留 30D，迁超出部分
  3) 有过期日 + 多库位 → 按到期日由远→近选（好货去DC，近期留FC）
  4) 有过期日 + 单库位 → 保留 30D，迁超出部分

Output: results/allocation_result_v3.csv
"""

import os, pymysql, pandas as pd, numpy as np
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime, date

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

OUT_DIR = Path(__file__).parent / "results"

# ── Parameters ────────────────────────────────────────────────
P = dict(
    top_trigger_d2s   = 28,   # TOP: FC D2S < X → 补货
    top_target_d2s    = 28,   # TOP: 补到 X 天
    mid_routine_d2s   = 28,   # MIDDLE: routine 触发
    mid_emergency_d2s = 14,   # MIDDLE: 紧急触发
    mid_target_d2s    = 28,   # MIDDLE: 补货目标
    mid_evict_d2s     = 30,   # MIDDLE: FC D2S > X → 迁出
    mid_daily_cap     = 500,  # MIDDLE: 每日最多处理 SKU 数
    tolerance_in      = 0.15, # 补回 tolerance（宁多）
    tolerance_out     = 0.15, # 迁出 tolerance（宁少）
    min_loc_qty       = 5,    # 忽略 < N 件的库位
)

def connect():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASS"), port=3306, charset="utf8mb4",
    )

IN3_TO_CBM = 1.0  # volume unit is in³, no conversion needed

# ── Load base data ─────────────────────────────────────────────
print("Loading segment SKU detail...", flush=True)
df_seg = pd.read_csv(OUT_DIR / "segment_sku_detail_20260917_2247.csv")
df_seg["item_number"] = df_seg["item_number"].astype(str)

print("Loading zone inventory...", flush=True)
df_zone = pd.read_csv(OUT_DIR / "zone_inventory.csv")
df_zone["item_number"] = df_zone["item_number"].astype(str)
df_zone["qty"] = pd.to_numeric(df_zone["qty"], errors="coerce").fillna(0)
df_zone["unit_volume_cbm"] = pd.to_numeric(df_zone["unit_volume_cbm"], errors="coerce").fillna(0) * IN3_TO_CBM

# Aggregate FC / SFC qty per SKU from zone_inventory
inv_agg = df_zone.groupby(["item_number", "zone"])["qty"].sum().unstack(fill_value=0).reset_index()
inv_agg.columns.name = None
if "FC"  not in inv_agg.columns: inv_agg["FC"]  = 0
if "SFC" not in inv_agg.columns: inv_agg["SFC"] = 0
inv_agg = inv_agg.rename(columns={"FC": "fc_qty_live", "SFC": "sfc_qty_live"})

# ── Merge segment + live inventory ────────────────────────────
df = df_seg.merge(inv_agg, on="item_number", how="left")
df["fc_qty_live"]  = df["fc_qty_live"].fillna(0)
df["sfc_qty_live"] = df["sfc_qty_live"].fillna(0)
df["avg_daily_orders"] = pd.to_numeric(df["avg_daily_orders"], errors="coerce").fillna(0)
df["unit_volume_cbm"]  = pd.to_numeric(df["unit_volume_cbm"],  errors="coerce").fillna(0) * IN3_TO_CBM

# Compute FC D2S (live)
df["fc_d2s"] = np.where(
    df["avg_daily_orders"] > 0,
    (df["fc_qty_live"] / df["avg_daily_orders"]).round(1),
    np.nan
)

# Classify segment (use default thresholds from loaded data)
df["seg"] = df["segment"]  # already in CSV

# ── Lot data for FC & SFC (wh=002) ────────────────────────────
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

# ── Lot summary per SKU × zone ─────────────────────────────────
def lot_summary(lot_df, zone):
    sub = lot_df[lot_df["zone"] == zone]
    out = sub.groupby("item_number").agg(
        has_exp       = ("exp_date",  lambda x: x.notna().any()),
        min_exp       = ("exp_date",  "min"),
        min_recv      = ("recv_date", "min"),
        has_legacy    = ("is_legacy", "max"),
        loc_count     = ("location_no", "nunique"),
    ).reset_index()
    out.columns = [f"{c}_{zone.lower()}" if c != "item_number" else c for c in out.columns]
    return out

lot_fc  = lot_summary(df_lot, "FC")
lot_sfc = lot_summary(df_lot, "SFC")

df = df.merge(lot_fc,  on="item_number", how="left")
df = df.merge(lot_sfc, on="item_number", how="left")

# ── Allocation Engine ──────────────────────────────────────────
today = pd.Timestamp(date.today())

records = []

for _, row in df.iterrows():
    sku         = row["item_number"]
    seg         = str(row.get("seg", "TAIL"))
    fc          = float(row["fc_qty_live"])
    sfc         = float(row["sfc_qty_live"])
    adl         = float(row["avg_daily_orders"])
    vol         = float(row["unit_volume_cbm"])
    fc_d2s      = row["fc_d2s"]  # may be nan

    action      = "KEEP"
    direction   = ""
    transfer_qty = 0.0
    reason      = ""
    priority    = 999

    target_qty = round(adl * P["mid_target_d2s"], 0) if adl > 0 else 0.0

    # ── TOP ───────────────────────────────────────────────────
    if seg == "TOP":
        if adl > 0 and (pd.isna(fc_d2s) or fc_d2s < P["top_trigger_d2s"]):
            avail  = sfc
            if avail > 0:
                xfer = avail  # TOP: full transfer back regardless of needed
                action       = "DC_TO_FC"
                direction    = "SFC→FC"
                transfer_qty = round(xfer, 0)
                reason       = f"FC D2S={fc_d2s:.1f} < {P['top_trigger_d2s']}, full SFC transfer back"

                # Priority: P1 EXP, P2 Aging, P3 D2S
                has_exp_fc  = row.get("has_exp_fc",  False)
                has_exp_sfc = row.get("has_exp_sfc", False)
                leg_fc      = row.get("has_legacy_fc",  0)
                leg_sfc     = row.get("has_legacy_sfc", 0)
                min_exp_fc  = row.get("min_exp_fc")
                min_exp_sfc = row.get("min_exp_sfc")
                min_recv_fc = row.get("min_recv_fc")
                min_recv_sfc= row.get("min_recv_sfc")

                # P1 EXP trigger
                exp_trigger = False
                if leg_sfc and not leg_fc:  exp_trigger = True  # SFC legacy, FC has lot
                elif has_exp_sfc and has_exp_fc:
                    if pd.notna(min_exp_sfc) and pd.notna(min_exp_fc):
                        exp_trigger = min_exp_sfc < min_exp_fc

                # P2 Aging trigger
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

            # DC→FC 补回
            if d2s < P["mid_routine_d2s"] and sfc > 0:
                needed = max(0.0, target_qty - fc)
                # tolerance: 宁多，接受 needed*(1-tol) 以上即补
                if needed > 0:
                    xfer = min(needed, sfc)
                    action       = "DC_TO_FC"
                    direction    = "SFC→FC"
                    transfer_qty = round(xfer, 0)
                    emerg        = d2s < P["mid_emergency_d2s"]
                    reason       = f"FC D2S={d2s:.1f} {'[EMERGENCY]' if emerg else '[ROUTINE]'}"
                    # Priority: D2S lowest → Batch Volume → Daily Sales
                    priority = d2s * 1000 - needed * vol  # lower D2S + bigger volume = higher priority

            # FC→DC 迁出
            elif d2s > P["mid_evict_d2s"]:
                keep_qty = round(adl * P["mid_evict_d2s"], 0)
                excess   = fc - keep_qty
                if excess > P["min_loc_qty"]:
                    action       = "FC_TO_DC"
                    direction    = "FC→SFC"
                    transfer_qty = round(excess, 0)
                    reason       = f"FC D2S={d2s:.1f} > {P['mid_evict_d2s']}, evict {excess:.0f}"
                    priority     = 500 + d2s  # 越高 D2S 越先迁

    # ── TAIL ─────────────────────────────────────────────────
    elif seg == "TAIL":
        if fc > 0:
            action       = "FC_TO_DC"
            direction    = "FC→SFC"
            transfer_qty = fc
            reason       = "TAIL: clear FC"
            priority     = 100  # 高优先

    records.append({
        "item_number":    sku,
        "seg":            seg,
        "biz_type":       "自营" if sku[0] == "1" else "FBY",
        "fc_qty":         fc,
        "sfc_qty":        sfc,
        "fc_d2s":         round(fc_d2s, 1) if not pd.isna(fc_d2s) else None,
        "avg_daily":      round(adl, 2),
        "unit_vol_in3":   vol,
        "action":         action,
        "direction":      direction,
        "transfer_qty":   transfer_qty,
        "transfer_vol_in3": round(transfer_qty * vol, 1),
        "target_fc_qty":  target_qty,
        "priority_score": round(priority, 3),
        "reason":         reason,
        # Lot info
        "fc_has_exp":     bool(row.get("has_exp_fc",  False)),
        "sfc_has_exp":    bool(row.get("has_exp_sfc", False)),
        "fc_min_exp":     str(row.get("min_exp_fc",  ""))[:10] if pd.notna(row.get("min_exp_fc")) else "",
        "sfc_min_exp":    str(row.get("min_exp_sfc", ""))[:10] if pd.notna(row.get("min_exp_sfc")) else "",
    })

result = pd.DataFrame(records)

# Apply MIDDLE daily cap (500 SKU DC→FC)
mid_in = result[(result["seg"] == "MIDDLE") & (result["action"] == "DC_TO_FC")].copy()
mid_in = mid_in.sort_values("priority_score").head(P["mid_daily_cap"])
mid_out_tail = result[~((result["seg"] == "MIDDLE") & (result["action"] == "DC_TO_FC"))]
result = pd.concat([mid_in, mid_out_tail]).sort_values(["seg", "priority_score"]).reset_index(drop=True)

# ── Summary ────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("V3 ALLOCATION RESULT SUMMARY")
print("=" * 65)
for seg in ["TOP", "MIDDLE", "TAIL"]:
    sub = result[result["seg"] == seg]
    for act in ["DC_TO_FC", "FC_TO_DC", "KEEP"]:
        s = sub[sub["action"] == act]
        if len(s) == 0: continue
        print(f"  [{seg}] {act:10s} → {len(s):>5,} SKUs  "
              f"qty={s['transfer_qty'].sum():>10,.0f}  "
              f"vol={s['transfer_vol_in3'].sum():>12,.0f} in³")

total_in  = result[result["action"]=="DC_TO_FC"]["transfer_qty"].sum()
total_out = result[result["action"]=="FC_TO_DC"]["transfer_qty"].sum()
print(f"\n  Total DC→FC: {total_in:>10,.0f} units")
print(f"  Total FC→DC: {total_out:>10,.0f} units")

# ── Save ───────────────────────────────────────────────────────
ts = datetime.now().strftime("%Y%m%d_%H%M")
out_path = OUT_DIR / f"allocation_result_v3_{ts}.csv"
result.to_csv(out_path, index=False)
print(f"\n✅ Saved: {out_path}  ({len(result):,} rows)")
