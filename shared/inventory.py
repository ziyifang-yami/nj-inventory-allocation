"""
shared/inventory.py — NJ 库存基数核心模块
==========================================
按 zone_id 正确区分 FC vs SFC，任何分配方案都调用此模块。

Zone 定义（NJ wh=002，已于 2026-09-17 确认）：
  FC  = zone_id IN (1, 2)
        zone_id=1: 传统 Bin Pick 区（B/C/G/H/J/K 等）
        zone_id=2: S 区（2026-09 迁入，S10511-S72958）
  SFC = zone_id = 0
        Stock/DC 存储区

只计 location_type IN (3, 4)：
  3 = STOCK 库位
  4 = BIN Pick 库位
"""

import os
import pymysql
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")


def _get_conn():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "rds.g3.yamibuy.net"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASS"),
        port=3306,
        charset="utf8mb4",
    )


def load_fc_sfc_inventory(warehouse: str = "002") -> pd.DataFrame:
    """
    按 zone_id 拉取 FC + SFC 当前库存（实时，来自 wh_inventory_transaction）。

    FC  = zone_id IN (1, 2) — Bin Pick zones
    SFC = zone_id = 0       — Storage/DC zone
    仅计 location_type IN (3, 4)

    Returns
    -------
    DataFrame with columns:
        item_number : str
        fc_qty      : float  — FC (zone 1+2) 当前库存
        sfc_qty     : float  — SFC (zone 0) 当前库存
    Only rows with fc_qty > 0 OR sfc_qty > 0 are returned.
    """
    print(f"  [inventory] Loading FC+SFC inventory (wh={warehouse}, zone-based)...",
          flush=True)
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT wit.item_number,
                   SUM(CASE WHEN sl.zone_id IN (1, 2) THEN wit.quantity ELSE 0 END) AS fc_qty,
                   SUM(CASE WHEN sl.zone_id = 0       THEN wit.quantity ELSE 0 END) AS sfc_qty
            FROM yamibuy_wh.wh_inventory_transaction wit
            JOIN yamibuy_wh.wh_storage_location sl
                ON sl.location_no      = wit.location_no
               AND sl.warehouse_number = wit.warehouse_number
            WHERE wit.warehouse_number = '{warehouse}'
              AND sl.location_type IN (3, 4)
              AND wit.quantity > 0
            GROUP BY wit.item_number
            HAVING fc_qty > 0 OR sfc_qty > 0
        """)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    conn.close()

    df = pd.DataFrame(rows, columns=cols)
    df["fc_qty"]  = pd.to_numeric(df["fc_qty"],  errors="coerce").fillna(0)
    df["sfc_qty"] = pd.to_numeric(df["sfc_qty"], errors="coerce").fillna(0)

    # Summary
    fc_sku  = (df["fc_qty"]  > 0).sum()
    sfc_sku = (df["sfc_qty"] > 0).sum()
    ovlp    = ((df["fc_qty"] > 0) & (df["sfc_qty"] > 0)).sum()
    fc_only = ((df["fc_qty"] > 0) & (df["sfc_qty"] == 0)).sum()
    sfc_only= ((df["fc_qty"] == 0) & (df["sfc_qty"] > 0)).sum()
    print(f"    Total SKUs  : {len(df):,}")
    print(f"    FC  (zone 1+2): {fc_sku:,} SKU  |  {df['fc_qty'].sum():,.0f} units")
    print(f"    SFC (zone 0  ): {sfc_sku:,} SKU  |  {df['sfc_qty'].sum():,.0f} units")
    print(f"    FC Only        : {fc_only:,} SKU")
    print(f"    SFC Only       : {sfc_only:,} SKU")
    print(f"    Overlap        : {ovlp:,} SKU")
    return df


def load_sku_volume(item_numbers: list[str]) -> pd.DataFrame:
    """
    Load per-SKU volume (cubic inches) and weight from xysc_wearhouse_goods.

    Returns
    -------
    DataFrame with columns: item_number, volume_in3, weight_g
    """
    if not item_numbers:
        return pd.DataFrame(columns=["item_number", "volume_in3", "weight_g"])

    conn = _get_conn()
    batch_size = 2000
    rows_all = []
    for i in range(0, len(item_numbers), batch_size):
        batch = tuple(str(x) for x in item_numbers[i:i + batch_size])
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ii.item_number,
                       COALESCE(wwg.volume, 0) AS volume_in3,
                       COALESCE(wwg.weight, 0) AS weight_g
                FROM yamibuy_im.im_item ii
                LEFT JOIN yamibuy_master.xysc_wearhouse_goods wwg
                    ON wwg.goods_id = ii.goods_id
                WHERE ii.item_number IN %s
            """, (batch,))
            rows_all.extend(cur.fetchall())
    conn.close()

    df = pd.DataFrame(rows_all, columns=["item_number", "volume_in3", "weight_g"])
    df["item_number"] = df["item_number"].astype(str)
    df["volume_in3"]  = pd.to_numeric(df["volume_in3"], errors="coerce").fillna(0)
    df["weight_g"]    = pd.to_numeric(df["weight_g"],   errors="coerce").fillna(0)
    return df
