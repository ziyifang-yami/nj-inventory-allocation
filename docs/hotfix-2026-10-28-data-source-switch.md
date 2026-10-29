# Hotfix: Data Source Switch — 2026-10-28

## 背景

Dashboard (8508) 对商品 `5021184261` (FBY) 显示 segment = TAIL，
但实际仓库出库数据显示该商品每日出库 5~13 单，属于 TOP 级别。

经过调查发现根本原因是 **Athena `trade_item_sale_agg_daily` 表对 FBY 商品存在系统性数据缺失**：
- FBY 商品（5开头）中 19.3% 的 SKU 在该表中完全无订单数据
- 其余 FBY SKU 的 order lines 也严重偏低
- 该商品 30 天内 Athena 只记录到 5 单，实际 MySQL WH 层有 ~192 单

---

## 根本原因分析

| 数据源 | 该商品 30天 order_lines | 说明 |
|--------|------------------------|------|
| `dwb_trade.trade_item_sale_agg_daily` (旧) | 5 | FBY ETL 缺失 |
| `yamibuy_master.xysc_order_*` | 12 | 包含主+子单，需去重 |
| `yamibuy_wh.wh_order_info` + `wh_order_goods` + `im_item` (新) | **191** | 正确，按 item_number join，解决 bundle/combo 问题 |

库存快照表同样存在问题：
- 旧：`yamibuy_inventory.inventory_record`（前台表，bundle 有问题）
- 新：`yamibuy_wh.wh_inventory_transaction_by_day`（WH 层，精确）

---

## 修改内容

### 文件：`data/loader.py` — `load()` 函数内的 `SQL_SALES`

#### 旧 SQL（已废弃）

```sql
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
    COUNT(DISTINCT i.inv_date)                              AS inv_days_30,
    COUNT(DISTINCT s.day_id)                               AS active_order_days,
    COALESCE(CAST(SUM(s.daily_orders) AS DOUBLE), 0)       AS order_lines_30d,
    COALESCE(CAST(SUM(s.daily_orders) AS DOUBLE) / 30.0, 0) AS avg_daily_orders,
    COALESCE(SUM(s.daily_sales), 0)                        AS sales_units_30d
FROM inv_30 i
LEFT JOIN sales s
    ON s.item_number = i.item_number AND s.day_id = i.inv_date
GROUP BY i.item_number
```

**Athena database**: `yamibuy_inventory`

#### 新 SQL（当前生效）

```sql
WITH inv_days AS (
    SELECT item_number, day_id AS inv_date,
           ROW_NUMBER() OVER (PARTITION BY item_number ORDER BY day_id DESC) AS rn
    FROM (
        SELECT DISTINCT item_number, day_id
        FROM yamibuy_wh.wh_inventory_transaction_by_day
        WHERE warehouse_number = '002'
          AND quantity > 0
          AND (item_number LIKE '1%' OR item_number LIKE '5%')
    ) t
),
inv_30 AS (SELECT item_number, inv_date FROM inv_days WHERE rn <= 30),
sales AS (
    SELECT ii.item_number, oi.order_id,
           CAST(DATE(DATE_FORMAT(FROM_UNIXTIME(oi.add_time), '%Y-%m-%d')) AS VARCHAR) AS sale_date,
           og.goods_number AS daily_sales
    FROM yamibuy_wh.wh_order_info oi
    JOIN yamibuy_wh.wh_order_goods og ON og.order_id = oi.order_id
    JOIN yamibuy_im.im_item ii ON og.goods_id = ii.goods_id
    WHERE oi.warehouse_number = '002'
      AND oi.order_status NOT IN (0, 3)
      AND (ii.item_number LIKE '1%' OR ii.item_number LIKE '5%')
),
sales_agg AS (
    SELECT item_number, sale_date,
           COUNT(DISTINCT order_id) AS daily_orders,
           SUM(daily_sales)         AS daily_sales
    FROM sales
    GROUP BY item_number, sale_date
)
SELECT
    i.item_number,
    COUNT(DISTINCT i.inv_date)                              AS inv_days_30,
    COUNT(DISTINCT s.sale_date)                             AS active_order_days,
    COALESCE(CAST(SUM(s.daily_orders) AS DOUBLE), 0)        AS order_lines_30d,
    COALESCE(CAST(SUM(s.daily_orders) AS DOUBLE) / 30.0, 0) AS avg_daily_orders,
    COALESCE(SUM(s.daily_sales), 0)                         AS sales_units_30d
FROM inv_30 i
LEFT JOIN sales_agg s
    ON s.item_number = i.item_number AND s.sale_date = i.inv_date
GROUP BY i.item_number
```

**Athena database**: `yamibuy_wh`（改变了）

#### 关键变更点

| 项目 | 旧 | 新 |
|------|----|----|
| 库存快照表 | `yamibuy_inventory.inventory_record` | `yamibuy_wh.wh_inventory_transaction_by_day` |
| 库存数量字段 | `available_qty > 0` + `virtual_qty = 0` | `quantity > 0` |
| 库存日期字段 | `dt` | `day_id`（string，分区键） |
| `inv_days` 去重 | 无（多库位重复计天） | 加 `DISTINCT item_number, day_id` 子查询去重 |
| 销售数据表 | `dwb_trade.trade_item_sale_agg_daily` | `yamibuy_wh.wh_order_info` + `wh_order_goods` + `yamibuy_im.im_item` |
| 订单 join 方式 | 直接按 item_number | 通过 `goods_id` join `im_item`，解决 bundle/combo 问题 |
| order_lines 定义 | `SUM(orders)` | `COUNT(DISTINCT order_id)`（主单去重） |
| Athena database | `yamibuy_inventory` | `yamibuy_wh` |

---

## 验证结果

调试脚本：`scripts/_debug_today/_test_new_sql.py`

SKU `5021184261` 新 SQL 结果：

| 字段 | 值 |
|------|-----|
| inv_days_30 | 30 ✅ |
| active_order_days | 30 |
| order_lines_30d | 191 |
| avg_daily_orders | 6.37 |
| sales_units_30d | 478 |
| **Segment** | **TOP** ✅（修复前为 TAIL） |

---

## 注意事项

1. **Athena 查询时间变长**：新 SQL join 了 3 张表（`wh_order_info`, `wh_order_goods`, `im_item`），全量扫描比原来慢。建议观察实际耗时，必要时考虑在 `wh_order_info` 上加 `add_time` 分区过滤（目前该表在 Athena 是否分区待确认）。

2. **cache 需要强制刷新**：切换后需删除 `results/athena_cache.csv` 和 `results/athena_cache_ts.txt`，让系统重跑 Athena 查询。

3. **服务器部署**：需要把更新后的 `data/loader.py` 推送到服务器 `/opt/inv-allocation/data/loader.py`，并重启 `inv-allocation.service`。

---

## 相关调查文件

| 文件 | 用途 |
|------|------|
| `scripts/_debug_today/_test_new_sql.py` | 验证新 SQL 的 Athena 测试脚本 |
| `scripts/_debug_today/_desc_wh_inv.py` | `wh_inventory_transaction_by_day` 表结构探查 |
| `scripts/_debug_today/_q.py` | `xysc_order_info` + `xysc_order_goods` 对比查询 |
| `scripts/_debug_today/_ob7.py` (服务器) | `wh_picking_order_item` 出库明细验证 |
| `scripts/_debug_today/_sqlite_fby_check.py` | FBY 覆盖率统计（确认旧 Athena 缺失比例） |
