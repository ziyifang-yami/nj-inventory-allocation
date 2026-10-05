"""
v4_DC_allocation/data/loader.py

Data loading layer for the NJ Inventory Allocation v4 system.
Adapts v3 load functions with the following v4-specific changes:

  - Shared _connect() helper (no repeated inline connection blocks)
  - inv_age_days derived from wh_inbound_batch.in_dtm (Unix timestamp)
  - case_qty parsed from im_item_extend.pieces_per_pack (varchar) — default 1
  - SFC zone filter: zone_id = 2, location_type IN (3,4), warehouse_number = '002'
    zone_id = 2 is the definitive SFC zone identifier
  - load_fc_lot_data(skus)  — FC lot-level data (for FC→SFC engine)
  - load_sfc_lot_data(skus) — SFC lot-level data with inv_age_days + case_qty
  - 30-min @st.cache_data for inventory / lot stats
  - 12-hour disk cache for Athena results in results/athena_cache.csv

Requirements covered: 9.1-9.5, 10.1-10.6, 11.1-11.4
"""

from __future__ import annotations

import glob
import os
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st


# ── Environment helper ────────────────────────────────────────────────────────

def _load_env() -> None:
    """Auto-detect .env: current dir first, then parent dirs up to 3 levels."""
    from dotenv import load_dotenv

    for parts in [(".",), ("..",), ("..", "..")]:
        env = Path(*parts) / ".env"
        if env.exists():
            load_dotenv(dotenv_path=env)
            return
    # If no .env found, dotenv will silently do nothing on the next call
    load_dotenv()


# ── Shared MySQL connection helper ────────────────────────────────────────────

def _connect():
    """
    Return an open pymysql connection using credentials from .env.

    Environment variables used:
        MYSQL_HOST  — hostname / IP
        MYSQL_USER  — username
        MYSQL_PASS  — password
        MYSQL_PORT  — port (default 3306)
        MYSQL_DB    — default database (optional)

    Raises pymysql.Error on connection failure.
    """
    import pymysql

    _load_env()
    port = int(os.getenv("MYSQL_PORT", "3306"))
    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASS"),
        port=port,
        charset="utf8mb4",
        connect_timeout=8,
    )
    return conn


# ── Inventory: FC/SFC quantities per SKU ─────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)  # 30-minute cache — Req 9.4
def load_inventory():
    """
    Real-time FC/SFC inventory quantities from MySQL.

    Returns
    -------
    (DataFrame, None) on success:
        Columns: item_number, fc_qty, sfc_qty
    (None, str) on failure — error message suitable for display in UI (Req 9.5).

    FC  = zone_id != 2, location_type IN (3,4)
    SFC = zone_id  = 2, location_type IN (3,4)
    """
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    wsli.item_number,
                    SUM(CASE
                        WHEN wsl.zone_id != 2
                            THEN wit.quantity ELSE 0
                        END) AS fc_qty,
                    SUM(CASE
                        WHEN wsl.zone_id = 2
                            THEN wit.quantity ELSE 0
                        END) AS sfc_qty
                FROM yamibuy_wh.wh_storage_location_item wsli
                JOIN yamibuy_wh.wh_inventory_transaction wit
                    ON  wit.item_number      = wsli.item_number
                    AND wit.warehouse_number = wsli.warehouse_number
                    AND wit.location_no      = wsli.location_no
                JOIN yamibuy_wh.wh_storage_location wsl
                    ON  wsl.location_no      = wsli.location_no
                    AND wsl.warehouse_number = wsli.warehouse_number
                JOIN yamibuy_wh.wh_location_size wls
                    ON  wls.size_id = wsl.size_id
                LEFT JOIN yamibuy_im.im_item ii
                    ON  ii.item_number = wsli.item_number
                WHERE wsli.warehouse_number = '002'
                  AND wit.quantity IS NOT NULL
                  AND wit.quantity != 0
                  AND wsl.location_type IN (3, 4)
                  AND wsl.size_id NOT IN (13, 33)
                  AND IFNULL(ii.business_type, 0) != 8
                GROUP BY wsli.item_number
                HAVING fc_qty > 0 OR sfc_qty > 0
            """)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()

        df = pd.DataFrame(rows, columns=cols)
        df["item_number"] = df["item_number"].astype(str)
        df["fc_qty"]  = pd.to_numeric(df["fc_qty"],  errors="coerce").fillna(0)
        df["sfc_qty"] = pd.to_numeric(df["sfc_qty"], errors="coerce").fillna(0)
        return df, None

    except Exception as exc:
        return None, str(exc)


# ── Capacity: FC/SFC location volume stats ────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)  # 30-minute cache
def load_capacity():
    """
    Real-time FC/SFC location capacity data from MySQL.

    Returns
    -------
    (DataFrame, None) on success:
        Columns: zone, loc_type, zone_type, storage_type, description,
                 fill_rate, total_locs, total_volume, allowable_cap,
                 used_volume, occupied_locs, free_volume, free_locs
    (None, str) on failure.
    """
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    CASE WHEN wsl.zone_id = 2 THEN 'SFC' ELSE 'FC' END AS zone,
                    CASE
                        WHEN wsl.location_type = 4 THEN 'Bin'
                        WHEN wsl.location_type = 3 THEN 'Stock'
                    END AS loc_type,
                    CONCAT(
                        CASE WHEN wsl.zone_id = 2 THEN 'SFC' ELSE 'FC' END,
                        '_',
                        CASE
                            WHEN wsl.location_type = 4 THEN 'Bin'
                            WHEN wsl.location_type = 3 THEN 'Stock'
                        END
                    ) AS zone_type,
                    wls.storage_type,
                    wls.description,
                    wls.fill_rate,
                    COUNT(DISTINCT wsl.location_no)              AS total_locs,
                    IFNULL(SUM(wls.volume), 0)                   AS total_volume,
                    IFNULL(SUM(wls.volume * wls.fill_rate), 0)   AS allowable_cap,
                    IFNULL(SUM(inv.sku_vol), 0)                  AS used_volume,
                    COUNT(DISTINCT CASE WHEN inv.sku_vol > 0
                                        THEN wsl.location_no END) AS occupied_locs
                FROM yamibuy_wh.wh_storage_location wsl
                LEFT JOIN yamibuy_wh.wh_location_size wls
                    ON wls.size_id = wsl.size_id
                LEFT JOIN (
                    SELECT wit.warehouse_number,
                           wit.location_no,
                           SUM(wwg.volume * wit.quantity) AS sku_vol
                    FROM yamibuy_wh.wh_inventory_transaction wit
                    JOIN yamibuy_im.im_item ii
                        ON ii.item_number = wit.item_number
                    JOIN yamibuy_master.xysc_wearhouse_goods wwg
                        ON wwg.goods_id = ii.goods_id
                    WHERE wit.warehouse_number = '002'
                      AND wit.quantity > 0
                    GROUP BY wit.warehouse_number, wit.location_no
                ) inv
                    ON  inv.warehouse_number = wsl.warehouse_number
                    AND inv.location_no      = wsl.location_no
                WHERE wsl.warehouse_number = '002'
                  AND wsl.location_type IN (3, 4)
                  AND wsl.size_id NOT IN (13, 33)
                GROUP BY 1, 2, 3, 4, 5, 6
            """)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()

        df = pd.DataFrame(rows, columns=cols)
        for c in ["allowable_cap", "used_volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
        df["free_volume"]    = df["allowable_cap"] - df["used_volume"]
        df["occupied_locs"]  = pd.to_numeric(df["occupied_locs"], errors="coerce").fillna(0).astype(int)
        df["free_locs"]      = (df["total_locs"] - df["occupied_locs"]).clip(lower=0)
        return df, None

    except Exception as exc:
        return None, str(exc)


# ── Sales / AOD data: Athena with 12-hour disk cache ─────────────────────────

@st.cache_data(ttl=43200, show_spinner=False)  # 12-hour in-memory cache (Req 10.3)
def load():
    """
    Sales/AOD data — disk cache first (results/athena_cache.csv),
    refreshes if cache is older than 12 h or missing.
    Falls back to the latest segment CSV if Athena is unavailable.

    Returns
    -------
    (DataFrame, str) — DataFrame of sales metrics and a human-readable
                        timestamp string ("updated YYYY-MM-DD HH:MM ET").

    Raises
    ------
    FileNotFoundError — if Athena fails AND no CSV fallback exists (Req 10.6).
    """
    import boto3
    import configparser
    from zoneinfo import ZoneInfo

    _load_env()

    CACHE_FILE    = Path("results/athena_cache.csv")
    CACHE_TS_FILE = Path("results/athena_cache_ts.txt")
    CACHE_MAX_AGE_H = 12

    # ── Disk cache check (Req 10.2, 10.3) ────────────────────
    if CACHE_FILE.exists() and CACHE_TS_FILE.exists():
        try:
            cached_ts = datetime.fromisoformat(CACHE_TS_FILE.read_text().strip())
            now_aware = datetime.now(ZoneInfo("America/New_York"))
            if cached_ts.tzinfo is None:
                cached_ts = cached_ts.replace(tzinfo=ZoneInfo("America/New_York"))
            age_h = (now_aware - cached_ts).total_seconds() / 3600
            if age_h < CACHE_MAX_AGE_H:
                df = pd.read_csv(CACHE_FILE)
                ts = cached_ts.astimezone(ZoneInfo("America/New_York")).strftime(
                    "updated %Y-%m-%d %H:%M ET"
                )
                return df, ts
        except Exception:
            pass  # corrupt cache → fall through to re-query

    # ── Athena query (Req 10.1, 10.4) ────────────────────────
    try:
        aws_profile = os.getenv("AWS_PROFILE")
        if not aws_profile:
            cfg = configparser.ConfigParser()
            cfg.read(os.path.expanduser("~/.aws/config"))
            sections = cfg.sections()
            if any(s in ("profile athena-prod", "athena-prod") for s in sections):
                aws_profile = "athena-prod"
            elif any(s in ("profile prod", "prod") for s in sections):
                aws_profile = "prod"
            else:
                aws_profile = "default"

        session  = boto3.Session(profile_name=aws_profile)
        athenac  = session.client("athena", region_name="us-west-2")
        workgroup = os.getenv("ATHENA_WORK_GROUP", "aiagent")
        s3_out    = (
            os.getenv("ATHENA_S3_STAGING", "s3://yami-athena-query-result/")
            + "aiagent/nj-v4-allocation/"
        )

        SQL_SALES = """
        WITH inv_days AS (
            SELECT item_number, dt AS inv_date,
                   ROW_NUMBER() OVER (PARTITION BY item_number ORDER BY dt DESC) AS rn
            FROM yamibuy_inventory.inventory_record
            WHERE warehouse_number = '002'
              AND virtual_qty = 0
              AND available_qty > 0
              AND (item_number LIKE '1%' OR item_number LIKE '5%')
        ),
        inv_30 AS (SELECT item_number, inv_date FROM inv_days WHERE rn <= 30),
        sales AS (
            SELECT item_number, day_id,
                   SUM(orders) AS daily_orders,
                   SUM(sales)  AS daily_sales
            FROM dwb_trade.trade_item_sale_agg_daily
            WHERE warehouse_number = '002'
              AND orders > 0
              AND (item_number LIKE '1%' OR item_number LIKE '5%')
            GROUP BY item_number, day_id
        )
        SELECT
            i.item_number,
            COUNT(DISTINCT i.inv_date)                             AS inv_days_30,
            COUNT(DISTINCT s.day_id)                               AS active_order_days,
            COALESCE(CAST(SUM(s.daily_orders) AS DOUBLE), 0)       AS order_lines_30d,
            COALESCE(CAST(SUM(s.daily_orders) AS DOUBLE) / 30.0, 0) AS avg_daily_orders,
            COALESCE(SUM(s.daily_sales), 0)                        AS sales_units_30d
        FROM inv_30 i
        LEFT JOIN sales s
            ON s.item_number = i.item_number AND s.day_id = i.inv_date
        GROUP BY i.item_number
        """

        resp = athenac.start_query_execution(
            QueryString=SQL_SALES,
            QueryExecutionContext={"Database": "yamibuy_inventory"},
            WorkGroup=workgroup,
            ResultConfiguration={"OutputLocation": s3_out},
        )
        qid = resp["QueryExecutionId"]

        for _ in range(150):  # poll up to ~5 minutes
            state = (
                athenac
                .get_query_execution(QueryExecutionId=qid)
                ["QueryExecution"]["Status"]["State"]
            )
            if state == "SUCCEEDED":
                break
            if state in ("FAILED", "CANCELLED"):
                reason = (
                    athenac
                    .get_query_execution(QueryExecutionId=qid)
                    ["QueryExecution"]["Status"]
                    .get("StateChangeReason", "no reason")
                )
                raise RuntimeError(f"Athena {state}: {reason}")
            time.sleep(2)
        else:
            raise TimeoutError("Athena query timed out after ~5 minutes")

        rows_all: list = []
        for page in athenac.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
            rows_all.extend(page["ResultSet"]["Rows"])

        cols = [c["VarCharValue"] for c in rows_all[0]["Data"]]
        data = [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows_all[1:]]
        df_sales = pd.DataFrame(data, columns=cols)
        for c in ["inv_days_30", "active_order_days", "order_lines_30d",
                  "avg_daily_orders", "sales_units_30d"]:
            df_sales[c] = pd.to_numeric(df_sales[c], errors="coerce").fillna(0)

        # Unit volume from MySQL (Req 11.1)
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT i.item_number,
                       wwg.volume AS unit_volume_cbm
                FROM yamibuy_im.im_item i
                JOIN yamibuy_master.xysc_wearhouse_goods wwg
                    ON i.goods_id = wwg.goods_id
                WHERE (i.item_number LIKE '1%%' OR i.item_number LIKE '5%%')
                  AND wwg.volume > 0
            """)
            vol_rows = cur.fetchall()
        conn.close()

        df_vol = pd.DataFrame(vol_rows, columns=["item_number", "unit_volume_cbm"])
        df_vol["unit_volume_cbm"] = pd.to_numeric(
            df_vol["unit_volume_cbm"], errors="coerce"
        ).fillna(0)

        df = df_sales.merge(df_vol, on="item_number", how="left")
        df["unit_volume_cbm"] = df["unit_volume_cbm"].fillna(0)
        df["avg_daily_sales"]  = df["sales_units_30d"] / 30
        df["daily_sales_cbm"]  = df["avg_daily_sales"] * df["unit_volume_cbm"]

        from zoneinfo import ZoneInfo
        now_nj = datetime.now(ZoneInfo("America/New_York"))
        ts = now_nj.strftime("updated %Y-%m-%d %H:%M ET")

        # Save to disk cache (Req 10.2)
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(CACHE_FILE, index=False)
            CACHE_TS_FILE.write_text(now_nj.isoformat())
        except Exception:
            pass  # cache write failure is non-fatal

        return df, ts

    except Exception as exc:
        # ── CSV fallback (Req 10.5, 10.6) ────────────────────
        files = sorted(glob.glob("results/segment_sku_detail_*.csv"))
        if not files:
            raise FileNotFoundError(
                f"Athena failed ({exc}) and no CSV fallback found in results/"
            )
        latest = files[-1]
        df = pd.read_csv(latest)
        m = re.search(r"(\d{8})_(\d{4})", latest)
        ts = (
            f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:8]} "
            f"{m.group(2)[:2]}:{m.group(2)[2:]} (CSV)"
        ) if m else "CSV"
        return df, ts


# ── FC lot data: lot-level location data for FC→SFC engine ───────────────────

def load_fc_lot_data(skus: list[str]):
    """
    Fetch FC lot-level location data for the given SKU list.

    Used by the FC→SFC engine (Location_Selector) and Queue_Builder for
    priority volume sorting. Req 11.2.

    Returns
    -------
    (DataFrame, None) on success:
        Columns:
          item_number, location_no, loc_type (Bin/Stock),
          qty, exp_date (datetime), inv_age_days (int),
          case_qty (int, parsed from pieces_per_pack),
          need_minimal_exp (int)
    (None, str) on failure.

    Zone filter: zone_id != 2, location_type IN (3,4), warehouse_number = '002'
    """
    if not skus:
        return pd.DataFrame(
            columns=["item_number", "location_no", "loc_type", "qty",
                     "exp_date", "inv_age_days", "case_qty", "need_minimal_exp"]
        ), None

    try:
        conn = _connect()
        # language=sql
        SQL = """
            SELECT
                li.item_number,
                li.location_no,
                CASE WHEN wsl.location_type = 4 THEN 'Bin'
                     WHEN wsl.location_type = 3 THEN 'Stock' END     AS loc_type,
                SUM(li.quantity)                                       AS qty,
                MAX(CASE WHEN ib.qc_ExpiredDate > 0
                         THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate))
                    END)                                               AS exp_date,
                CAST(DATEDIFF(CURDATE(), DATE(FROM_UNIXTIME(
                    CASE WHEN lm.lot_id = 1 OR lm.lot_id IS NULL
                         THEN NULLIF(ib.in_dtm, 0)
                         ELSE NULLIF(lm.receive_time, 0)
                    END
                ))) AS SIGNED)                                         AS inv_age_days,
                CASE WHEN wis.spec IS NULL OR wis.spec = '' THEN 1
                     ELSE CAST(wis.spec AS UNSIGNED)
                END                                                    AS case_qty,
                MAX(IFNULL(iie.need_minimal_exp, 0))                   AS need_minimal_exp
            FROM yamibuy_wh.wh_lot_inventory li
            JOIN yamibuy_wh.wh_storage_location wsl
                ON  wsl.location_no      = li.location_no
                AND wsl.warehouse_number = li.warehouse_number
            LEFT JOIN yamibuy_wh.wh_lot_master lm
                ON  lm.lot_id = li.lot_id
            LEFT JOIN yamibuy_wh.wh_inbound_batch ib
                ON  ib.lot_id = li.lot_id
            LEFT JOIN yamibuy_wh.wh_item_setting wis
                ON  wis.item_number      = li.item_number
                AND wis.warehouse_number = '002'
            LEFT JOIN yamibuy_im.im_item_extend iie
                ON  iie.item_number = li.item_number
            WHERE li.warehouse_number = '002'
              AND li.quantity > 0
              AND wsl.location_type IN (3, 4)
              AND wsl.zone_id != 2
              AND li.item_number IN ({ph})
            GROUP BY li.item_number, li.location_no, wsl.location_type,
                     lm.lot_id, lm.receive_time, wis.spec
            HAVING SUM(li.quantity) > 0
        """

        all_rows: list = []
        all_cols: list | None = None
        chunks = [skus[i : i + 500] for i in range(0, len(skus), 500)]

        with conn.cursor() as cur:
            for chunk in chunks:
                ph = ",".join(["%s"] * len(chunk))
                cur.execute(SQL.format(ph=ph), chunk)
                if all_cols is None:
                    all_cols = [d[0] for d in cur.description]
                all_rows.extend(cur.fetchall())
        conn.close()

        df = pd.DataFrame(all_rows, columns=all_cols)
        df["item_number"]      = df["item_number"].astype(str)
        df["qty"]              = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        df["exp_date"]         = pd.to_datetime(df["exp_date"], errors="coerce")
        df["inv_age_days"]     = pd.to_numeric(df["inv_age_days"], errors="coerce").fillna(0).astype(int)
        df["need_minimal_exp"] = pd.to_numeric(df["need_minimal_exp"], errors="coerce").fillna(0).astype(int)
        df["case_qty"]         = pd.to_numeric(df["case_qty"], errors="coerce").fillna(1).clip(lower=1).astype(int)
        return df, None

    except Exception as exc:
        return None, str(exc)


# ── SFC lot data: lot-level location data for SFC→FC engine ──────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def _sfc_lot_disk_cache():
    """
    No-arg wrapper — reads SFC lot parquet if cache is valid.
    No-argument means @st.cache_data key never changes, avoiding
    the overhead of hashing a 5K+ item tuple on every call.
    Returns (DataFrame, True) if hit, (None, False) if miss.
    """
    CACHE_FILE = Path("results/sfc_lot_cache.parquet")
    CACHE_TS   = Path("results/sfc_lot_cache_ts.txt")
    if CACHE_FILE.exists() and CACHE_TS.exists():
        try:
            from datetime import datetime
            _UTC = ZoneInfo("UTC")
            cached_ts = datetime.fromisoformat(CACHE_TS.read_text().strip())
            if cached_ts.tzinfo is None:
                cached_ts = cached_ts.replace(tzinfo=_UTC)
            age_min = (datetime.now(_UTC) - cached_ts.astimezone(_UTC)).total_seconds() / 60
            if age_min < 120:  # 2 hours TTL
                return pd.read_parquet(CACHE_FILE), True
        except Exception:
            pass
    return None, False


@st.cache_data(ttl=1800, show_spinner=False)  # 30-minute memory cache
def load_sfc_lot_data(skus_tuple: tuple):
    """
    Fetch SFC lot-level location data for the given SKU list.
    Fast path: uses _sfc_lot_disk_cache() to avoid hashing large tuples.
    """
    import hashlib
    skus = list(skus_tuple)
    if not skus:
        return pd.DataFrame(
            columns=["item_number", "location_no", "loc_type", "qty",
                     "exp_date", "inv_age_days", "case_qty", "need_minimal_exp"]
        ), None

    # Fast path: check disk cache via no-arg function (no tuple hashing overhead)
    _cached_df, _hit = _sfc_lot_disk_cache()
    if _hit and _cached_df is not None:
        return _cached_df, None

    # ── Disk cache check (30 min) ─────────────────────────────
    CACHE_FILE = Path("results/sfc_lot_cache.parquet")
    CACHE_TS   = Path("results/sfc_lot_cache_ts.txt")
    _CACHE_MAX_AGE_MIN = 120  # 2 hours — gives buffer for cron refresh cycle

    if CACHE_FILE.exists() and CACHE_TS.exists():
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            _UTC = ZoneInfo("UTC")
            cached_ts = datetime.fromisoformat(CACHE_TS.read_text().strip())
            if cached_ts.tzinfo is None:
                cached_ts = cached_ts.replace(tzinfo=_UTC)
            age_min = (datetime.now(_UTC) - cached_ts.astimezone(_UTC)).total_seconds() / 60
            if age_min < _CACHE_MAX_AGE_MIN:
                df = pd.read_parquet(CACHE_FILE)
                return df, None
        except Exception:
            pass  # corrupt cache → fall through to re-query

    try:
        conn = _connect()
        # language=sql
        SQL = """
            SELECT
                li.item_number,
                li.location_no,
                CASE WHEN wsl.location_type = 4 THEN 'Bin'
                     WHEN wsl.location_type = 3 THEN 'Stock' END     AS loc_type,
                SUM(li.quantity)                                       AS qty,
                MAX(CASE WHEN ib.qc_ExpiredDate > 0
                         THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate))
                    END)                                               AS exp_date,
                MAX(CAST(DATEDIFF(CURDATE(), DATE(FROM_UNIXTIME(
                    CASE WHEN lm.lot_id = 1 OR lm.lot_id IS NULL
                         THEN NULLIF(ib.in_dtm, 0)
                         ELSE NULLIF(lm.receive_time, 0)
                    END
                ))) AS SIGNED))                                        AS inv_age_days,
                CASE WHEN wis.spec IS NULL OR wis.spec = '' THEN 1
                     ELSE CAST(wis.spec AS UNSIGNED)
                END                                                    AS case_qty,
                MAX(IFNULL(iie.need_minimal_exp, 0))                   AS need_minimal_exp
            FROM yamibuy_wh.wh_lot_inventory li
            JOIN yamibuy_wh.wh_storage_location wsl
                ON  wsl.location_no      = li.location_no
                AND wsl.warehouse_number = li.warehouse_number
            LEFT JOIN yamibuy_wh.wh_lot_master lm
                ON  lm.lot_id = li.lot_id
            LEFT JOIN yamibuy_wh.wh_inbound_batch ib
                ON  ib.lot_id = li.lot_id
            LEFT JOIN yamibuy_wh.wh_item_setting wis
                ON  wis.item_number      = li.item_number
                AND wis.warehouse_number = '002'
            LEFT JOIN yamibuy_im.im_item_extend iie
                ON  iie.item_number = li.item_number
            WHERE li.warehouse_number = '002'
              AND li.quantity > 0
              AND wsl.location_type IN (3, 4)
              AND wsl.zone_id = 2
              AND li.item_number IN ({ph})
            GROUP BY li.item_number, li.location_no, wsl.location_type, wis.spec
            HAVING SUM(li.quantity) > 0
        """

        all_rows: list = []
        all_cols: list | None = None
        chunks = [skus[i : i + 500] for i in range(0, len(skus), 500)]

        with conn.cursor() as cur:
            for chunk in chunks:
                ph = ",".join(["%s"] * len(chunk))
                cur.execute(SQL.format(ph=ph), chunk)
                if all_cols is None:
                    all_cols = [d[0] for d in cur.description]
                all_rows.extend(cur.fetchall())
        conn.close()

        df = pd.DataFrame(all_rows, columns=all_cols)
        df["item_number"]      = df["item_number"].astype(str)
        df["qty"]              = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        df["exp_date"]         = pd.to_datetime(df["exp_date"], errors="coerce")
        df["inv_age_days"]     = pd.to_numeric(df["inv_age_days"], errors="coerce").fillna(0).astype(int)
        df["need_minimal_exp"] = pd.to_numeric(df["need_minimal_exp"], errors="coerce").fillna(0).astype(int)
        df["case_qty"]         = pd.to_numeric(df["case_qty"], errors="coerce").fillna(1).clip(lower=1).astype(int)

        # ── Write disk cache ──────────────────────────────────
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(CACHE_FILE, index=False)
            CACHE_TS.write_text(datetime.now(ZoneInfo("UTC")).isoformat())
        except Exception:
            pass  # non-fatal

        return df, None

    except Exception as exc:
        return None, str(exc)


# ── SKU location stats: FC volume + location count per SKU ───────────────────

@st.cache_data(ttl=1800, show_spinner=False)  # 30-minute cache (Req 11.4)
def load_sku_loc_stats(skus_tuple: tuple):
    """
    For each SKU, return total FC volume (qty × unit_vol) and occupied FC location count.

    Used for priority sorting in the FC→SFC Queue_Builder.
    Accepts a TUPLE (not list) so @st.cache_data can hash it correctly.

    Returns
    -------
    DataFrame with columns: item_number, loc_count, total_vol
    Returns an empty DataFrame on error (non-fatal — callers can fall back to 0-volume sorting).
    """
    skus = list(skus_tuple)
    if not skus:
        return pd.DataFrame(columns=["item_number", "loc_count", "total_vol"])

    SQL = """
        SELECT
            li.item_number,
            COUNT(DISTINCT li.location_no)             AS loc_count,
            SUM(li.quantity * COALESCE(wwg.volume, 0)) AS total_vol
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl
            ON  wsl.location_no      = li.location_no
            AND wsl.warehouse_number = li.warehouse_number
        LEFT JOIN yamibuy_im.im_item ii
            ON  ii.item_number = li.item_number
        LEFT JOIN yamibuy_master.xysc_wearhouse_goods wwg
            ON  wwg.goods_id = ii.goods_id
        WHERE li.warehouse_number = '002'
          AND li.quantity > 0
          AND wsl.location_type IN (3, 4)
          AND wsl.zone_id != 2
          AND li.item_number IN ({ph})
        GROUP BY li.item_number
    """

    try:
        conn = _connect()
        all_rows: list = []
        all_cols: list | None = None
        chunks = [skus[i : i + 500] for i in range(0, len(skus), 500)]

        with conn.cursor() as cur:
            for chunk in chunks:
                ph = ",".join(["%s"] * len(chunk))
                cur.execute(SQL.format(ph=ph), chunk)
                if all_cols is None:
                    all_cols = [d[0] for d in cur.description]
                all_rows.extend(cur.fetchall())
        conn.close()

        df = pd.DataFrame(all_rows, columns=all_cols)
        df["item_number"] = df["item_number"].astype(str)
        df["loc_count"]   = pd.to_numeric(df["loc_count"], errors="coerce").fillna(0).astype(int)
        df["total_vol"]   = pd.to_numeric(df["total_vol"], errors="coerce").fillna(0)
        return df

    except Exception:
        return pd.DataFrame(columns=["item_number", "loc_count", "total_vol"])


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_case_qty(raw) -> int:
    """
    Parse an integer case quantity from im_item_extend.pieces_per_pack (varchar).

    Examples:
        "24"          → 24
        "12 pcs"      → 12
        "6×4"         → 6   (first number)
        None / ""     → 1   (default)
        "N/A"         → 1   (default)

    Returns 1 on any parse failure so that rounding logic degrades gracefully.
    """
    if raw is None:
        return 1
    s = str(raw).strip()
    if not s:
        return 1
    m = re.search(r"\d+", s)
    if not m:
        return 1
    try:
        val = int(m.group())
        return val if val > 0 else 1
    except ValueError:
        return 1


# ── FC exp date summary: per-SKU MAX and MIN exp date across all FC lots ──────

@st.cache_data(ttl=1800, show_spinner=False)
def load_fc_exp_summary(skus_tuple: tuple):
    """
    For each SKU in skus_tuple, return MAX and MIN fc exp_date across all FC lots.

    Optimization: if skus_tuple is empty, returns empty DataFrame.
    Caller should pre-filter to only EXP-trigger SKUs (need_minimal_exp=1)
    to avoid querying all 14K+ SKUs.
    """
    skus = list(skus_tuple)
    if not skus:
        return pd.DataFrame(columns=["item_number", "fc_max_exp_date", "fc_min_exp_date"])

    SQL = """
        SELECT
            li.item_number,
            MAX(CASE WHEN ib.qc_ExpiredDate > 0
                     THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) END) AS fc_max_exp_date,
            MIN(CASE WHEN ib.qc_ExpiredDate > 0
                     THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) END) AS fc_min_exp_date
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl
            ON  wsl.location_no      = li.location_no
            AND wsl.warehouse_number = li.warehouse_number
        LEFT JOIN yamibuy_wh.wh_inbound_batch ib ON ib.lot_id = li.lot_id
        WHERE li.warehouse_number = '002'
          AND li.quantity > 0
          AND wsl.location_type IN (3, 4)
          AND wsl.zone_id != 2
          AND li.item_number IN ({ph})
        GROUP BY li.item_number
    """

    try:
        conn = _connect()
        all_rows: list = []
        all_cols: list | None = None
        chunks = [skus[i : i + 500] for i in range(0, len(skus), 500)]

        with conn.cursor() as cur:
            for chunk in chunks:
                ph = ",".join(["%s"] * len(chunk))
                cur.execute(SQL.format(ph=ph), chunk)
                if all_cols is None:
                    all_cols = [d[0] for d in cur.description]
                all_rows.extend(cur.fetchall())
        conn.close()

        df = pd.DataFrame(all_rows, columns=all_cols)
        df["item_number"]      = df["item_number"].astype(str)
        df["fc_max_exp_date"]  = pd.to_datetime(df["fc_max_exp_date"], errors="coerce")
        df["fc_min_exp_date"]  = pd.to_datetime(df["fc_min_exp_date"], errors="coerce")
        return df

    except Exception:
        return pd.DataFrame(columns=["item_number", "fc_max_exp_date", "fc_min_exp_date"])

# ── FC exp summary (fast): single query, no IN clause ─────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def load_fc_exp_summary_all():
    """
    Load FC exp dates for ALL need_minimal_exp=1 SKUs in ONE query.
    Uses subquery approach + disk cache. First run ~5-10s, subsequent <0.1s.
    """
    from pathlib import Path
    CACHE_FILE = Path("results/fc_exp_all_cache.parquet")
    CACHE_TS   = Path("results/fc_exp_all_cache_ts.txt")
    _MAX_AGE_MIN = 30

    if CACHE_FILE.exists() and CACHE_TS.exists():
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            cached_ts = datetime.fromisoformat(CACHE_TS.read_text().strip())
            if cached_ts.tzinfo is None:
                cached_ts = cached_ts.replace(tzinfo=ZoneInfo("America/New_York"))
            age_min = (datetime.now(ZoneInfo("America/New_York")) - cached_ts).total_seconds() / 60
            if age_min < _MAX_AGE_MIN:
                df = pd.read_parquet(CACHE_FILE)
                return df
        except Exception:
            pass

    SQL = """
        SELECT
            li.item_number,
            MAX(CASE WHEN ib.qc_ExpiredDate > 0
                     THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) END) AS fc_max_exp_date,
            MIN(CASE WHEN ib.qc_ExpiredDate > 0
                     THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate)) END) AS fc_min_exp_date
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl
            ON  wsl.location_no      = li.location_no
            AND wsl.warehouse_number = li.warehouse_number
        LEFT JOIN yamibuy_wh.wh_inbound_batch ib
            ON  ib.lot_id = li.lot_id
        JOIN yamibuy_im.im_item_extend iie
            ON  iie.item_number = li.item_number
            AND iie.need_minimal_exp = 1
        WHERE li.warehouse_number = '002'
          AND li.quantity > 0
          AND wsl.location_type IN (3, 4)
          AND wsl.zone_id != 2
        GROUP BY li.item_number
        HAVING fc_max_exp_date IS NOT NULL
    """
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(SQL)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()

        df = pd.DataFrame(rows, columns=cols)
        df["item_number"]     = df["item_number"].astype(str)
        df["fc_max_exp_date"] = pd.to_datetime(df["fc_max_exp_date"], errors="coerce")
        df["fc_min_exp_date"] = pd.to_datetime(df["fc_min_exp_date"], errors="coerce")

        # Write disk cache
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(CACHE_FILE, index=False)
            CACHE_TS.write_text(datetime.now(ZoneInfo("America/New_York")).isoformat())
        except Exception:
            pass

        return df

    except Exception:
        return pd.DataFrame(columns=["item_number", "fc_max_exp_date", "fc_min_exp_date"])

@st.cache_data(ttl=1800, show_spinner=False)
def load_sfc_lot_data_all():
    """
    Fetch ALL SFC lot data in ONE query — no IN clause, no chunking.

    Much faster than load_sfc_lot_data(skus_tuple) which fires multiple
    chunked IN-clause queries. One full-table scan is cheaper than 29 round trips.

    Results are disk-cached for 30 minutes. The app then filters to only
    the relevant SKUs in Python (fast, no MySQL round trip).

    Returns (DataFrame, None) or (None, error_str).
    """
    from pathlib import Path

    CACHE_FILE = Path("results/sfc_lot_all_cache.parquet")
    CACHE_TS   = Path("results/sfc_lot_all_cache_ts.txt")
    _MAX_AGE_MIN = 30

    # Disk cache check
    if CACHE_FILE.exists() and CACHE_TS.exists():
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            cached_ts = datetime.fromisoformat(CACHE_TS.read_text().strip())
            if cached_ts.tzinfo is None:
                cached_ts = cached_ts.replace(tzinfo=ZoneInfo("America/New_York"))
            age_min = (datetime.now(ZoneInfo("America/New_York")) - cached_ts).total_seconds() / 60
            if age_min < _MAX_AGE_MIN:
                df = pd.read_parquet(CACHE_FILE)
                return df, None
        except Exception:
            pass

    SQL = """
        SELECT
            li.item_number,
            li.location_no,
            CASE WHEN wsl.location_type = 4 THEN 'Bin'
                 WHEN wsl.location_type = 3 THEN 'Stock' END     AS loc_type,
            SUM(li.quantity)                                       AS qty,
            MAX(CASE WHEN ib.qc_ExpiredDate > 0
                     THEN DATE(FROM_UNIXTIME(ib.qc_ExpiredDate))
                END)                                               AS exp_date,
            CAST(DATEDIFF(CURDATE(), DATE(FROM_UNIXTIME(
                CASE WHEN lm.lot_id = 1 OR lm.lot_id IS NULL
                     THEN NULLIF(ib.in_dtm, 0)
                     ELSE NULLIF(lm.receive_time, 0)
                END
            ))) AS SIGNED)                                         AS inv_age_days,
            CASE WHEN wis.spec IS NULL OR wis.spec = '' THEN 1
                 ELSE CAST(wis.spec AS UNSIGNED)
            END                                                    AS case_qty,
            MAX(IFNULL(iie.need_minimal_exp, 0))                   AS need_minimal_exp
        FROM yamibuy_wh.wh_lot_inventory li
        JOIN yamibuy_wh.wh_storage_location wsl
            ON  wsl.location_no      = li.location_no
            AND wsl.warehouse_number = li.warehouse_number
            AND wsl.location_type   IN (3, 4)
            AND wsl.zone_id = 2
        LEFT JOIN yamibuy_wh.wh_lot_master lm
            ON  lm.lot_id = li.lot_id
        LEFT JOIN yamibuy_wh.wh_inbound_batch ib
            ON  ib.lot_id = li.lot_id
        LEFT JOIN yamibuy_wh.wh_item_setting wis
            ON  wis.item_number      = li.item_number
            AND wis.warehouse_number = '002'
        LEFT JOIN yamibuy_im.im_item_extend iie
            ON  iie.item_number = li.item_number
        WHERE li.warehouse_number = '002'
          AND li.quantity > 0
        GROUP BY li.item_number, li.location_no, wsl.location_type,
                 lm.lot_id, lm.receive_time, wis.spec
        HAVING SUM(li.quantity) > 0
    """

    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(SQL)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()

        df = pd.DataFrame(rows, columns=cols)
        df["item_number"]      = df["item_number"].astype(str)
        df["qty"]              = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
        df["exp_date"]         = pd.to_datetime(df["exp_date"], errors="coerce")
        df["inv_age_days"]     = pd.to_numeric(df["inv_age_days"], errors="coerce").fillna(0).astype(int)
        df["need_minimal_exp"] = pd.to_numeric(df["need_minimal_exp"], errors="coerce").fillna(0).astype(int)
        df["case_qty"]         = pd.to_numeric(df["case_qty"], errors="coerce").fillna(1).clip(lower=1).astype(int)

        # Save to disk cache
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(CACHE_FILE, index=False)
            CACHE_TS.write_text(datetime.now(ZoneInfo("America/New_York")).isoformat())
        except Exception:
            pass

        return df, None

    except Exception as exc:
        return None, str(exc)


# ── Pending putaway: items received but not yet put away ──────────────────────

@st.cache_data(ttl=1800, show_spinner=False)  # 30-min cache — same as cron cycle
def load_pending_putaway():
    """
    Query NJ warehouse for items received but not yet put away.

    Items sitting in RECG (location_type=1) staging locations are received
    goods waiting to be moved to their final Stock/Bin location.

    Two-query approach to avoid slow full-table scan on wh_inventory_transaction_log:
      Q1: Fast — get all RECG items from wh_inventory_transaction (indexed)
      Q2: Fast — get PO references from wh_inventory_transaction_log
          filtered by the specific item_numbers found in Q1 (~50-150 items)

    Returns
    -------
    (DataFrame, None) on success:
        Columns: item_number, location_no, waiting_putaway_qty, po_reference, last_update
    (None, str) on failure.
    """
    try:
        conn = _connect()

        # ── Q1: Get all items currently in RECG staging (fast, indexed) ──────
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    it.item_number,
                    it.location_no,
                    SUM(it.quantity)                AS waiting_putaway_qty,
                    FROM_UNIXTIME(MAX(it.edit_dtm)) AS last_update
                FROM yamibuy_wh.wh_inventory_transaction it
                JOIN yamibuy_wh.wh_storage_location sl
                    ON  sl.warehouse_number = it.warehouse_number
                    AND sl.location_no      = it.location_no
                WHERE it.warehouse_number = '002'
                  AND it.quantity > 0
                  AND sl.location_type = 1
                GROUP BY it.item_number, it.location_no
                HAVING SUM(it.quantity) > 0
                ORDER BY waiting_putaway_qty DESC
            """)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()

        if not rows:
            conn.close()
            return pd.DataFrame(
                columns=["item_number", "location_no", "waiting_putaway_qty", "po_reference", "last_update"]
            ), None

        df = pd.DataFrame(rows, columns=cols)
        df["item_number"]         = df["item_number"].astype(str)
        df["waiting_putaway_qty"] = pd.to_numeric(df["waiting_putaway_qty"], errors="coerce").fillna(0).astype(int)
        df["last_update"]         = pd.to_datetime(df["last_update"], errors="coerce")

        # ── Q2: Get PO references only for the ~50-150 items found (fast) ────
        item_list = df["item_number"].unique().tolist()
        ph = ",".join(["%s"] * len(item_list))
        try:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT
                        item_number,
                        GROUP_CONCAT(DISTINCT reference_id
                            ORDER BY reference_id SEPARATOR ', ') AS po_reference
                    FROM yamibuy_wh.wh_inventory_transaction_log
                    WHERE warehouse_number = '002'
                      AND item_number IN ({ph})
                      AND reference_type IN (
                          'PO System(Sellable)',
                          'Seller shipment System(Sellable)'
                      )
                    GROUP BY item_number
                """, item_list)
                po_cols = [d[0] for d in cur.description]
                po_rows = cur.fetchall()
            df_po = pd.DataFrame(po_rows, columns=po_cols)
            df_po["item_number"] = df_po["item_number"].astype(str)
            df = df.merge(df_po, on="item_number", how="left")
        except Exception:
            # PO reference is nice-to-have; don't fail the whole tab
            df["po_reference"] = None

        conn.close()
        return df, None

    except Exception as exc:
        return None, str(exc)
