# Hotfix: Data Source Switch — 2026-10-28

## 背景

Dashboard (8508) 对商品 `5021184261` (FBY) 显示 segment = TAIL，
但实际仓库出库数据显示该商品每日出库 5~13 单，属于 TOP 级别。

根本原因：
1. **Athena `trade_item_sale_agg_daily` 对 FBY 商品存在系统性数据缺失** — 19.3% 的 FBY SKU 完全无数据，其余也严重偏低（该商品 Athena 只有 5 单，WH 层实际 191 单）
2. **`yamibuy_inventory.inventory_record` 库存快照来自前台系统** — 未过滤可售库位，包含临时/过渡库位库存，且存在 bundle/combo 计数问题

---

## 修改文件

### `data/loader.py` — `load()` 函数内的 `SQL_SALES`

#### 变更对比

| 项目 | 旧 | 新 |
|------|----|----|
| **库存快照表** | `yamibuy_inventory.inventory_record` | `yamibuy_wh.wh_inventory_transaction_by_day` |
| **库存可售过滤** | 无（包含所有库位） | JOIN `wh_storage_location`，只取 `location_type IN (3, 4)` |
| **库存数量字段** | `available_qty > 0` + `virtual_qty = 0` | `quantity > 0` |
| **库存日期字段** | `dt` | `day_id`（string，Athena 分区键） |
| **inv_days 去重** | 无（多库位重复计天） | 加 `DISTINCT item_number, day_id` 子查询去重 |
| **销售数据表** | `dwb_trade.trade_item_sale_agg_daily` | `yamibuy_wh.wh_order_info` + `wh_order_goods` + `yamibuy_im.im_item` |
| **order_status 过滤** | `NOT IN (0, 3)`（错误） | `IN (1, 5)` — 已确认 + 已分单，排除退货(4) |
| **订单 join 方式** | 直接按 item_number | 通过 `goods_id` join `im_item`，解决 bundle/combo 问题 |
| **order_lines 定义** | `SUM(orders)` | `COUNT(DISTINCT order_id)` — 主单去重 |
| **Athena database** | `yamibuy_inventory` | `yamibuy_wh` |

#### 字段说明

**`wh_order_info.warehouse_number`**：履约仓（分仓优化后确定），不是购物车仓（购物车仓在 `cart_warehouse_number`）。`warehouse_number = '002'` 过滤的是 NJ 仓履约的订单。

**`wh_order_info.add_time`**：下单时间（ET Unix 秒）。下单即扣库存，与库存快照日期对齐，语义一致。

**`wh_order_info.order_status` 枚举**：

| 值 | 含义 | 是否计入销量 |
|----|------|------------|
| 1 | 已确认 | ✅ |
| 5 | 已分单（已下发到履约仓，正常流转） | ✅ |
| 4 | 退货 | ❌ |

**`wh_storage_location.location_type`**（枚举定义在 `yamibuy_wh.wh_location_type`）：
- `3` = Stock（标准库位，可售）
- `4` = Bin（拣货库位，可售）
- 其他类型不计入可售库存快照

**`wh_inventory_transaction_by_day` 快照机制**：T+1，每天凌晨约 03:07~03:24 ET 写入前一天数据（`day_id = '2026-10-27'` 在 `2026-10-28 03:13 ET` 完成写入）。`min(edit_dtm)` 为 2022 年是历史遗留行（库位长期不变时不更新），不代表快照时间。

**`allocated_qty` 字段不纳入过滤条件**：`allocated_qty` 是已被订单预占、等待拣货的数量，是 `quantity` 的子集（物理库存里的一部分）。我们用 `quantity > 0` 判断"这天有没有在库库存"，目的是统计 `inv_days_30`。如果改成 `quantity - allocated_qty > 0`，会把"全部被预占但仍在库"的天排除，反而低估在库天数。因此保持 `quantity > 0` 不变。

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

**Athena database（旧）**: `yamibuy_inventory`

#### 新 SQL（当前生效）

```sql
WITH inv_days AS (
    SELECT item_number, day_id AS inv_date,
           ROW_NUMBER() OVER (PARTITION BY item_number ORDER BY day_id DESC) AS rn
    FROM (
        SELECT DISTINCT t.item_number, t.day_id
        FROM yamibuy_wh.wh_inventory_transaction_by_day t
        JOIN yamibuy_wh.wh_storage_location wsl
            ON  wsl.location_no      = t.location_no
            AND wsl.warehouse_number = t.warehouse_number
        WHERE t.warehouse_number = '002'
          AND t.quantity > 0
          AND wsl.location_type IN (3, 4)
          AND (t.item_number LIKE '1%' OR t.item_number LIKE '5%')
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
      AND oi.order_status IN (1, 5)
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

**Athena database**: `yamibuy_wh`

---

### `refresh_dashboard_cache.py`

Windows 兼容修复（本地开发用，服务器无影响）：
- `fcntl` — 改为条件导入，Windows 用 no-op 替代
- `signal.SIGALRM` — 改为 `sys.platform == "win32"` 判断，Windows 跳过 timeout 逻辑

---

## 验证结果

SKU `5021184261` 新 SQL 结果：

| 字段 | 旧（Athena agg 表） | 新（WH 层） |
|------|---------------------|-------------|
| inv_days_30 | 30 | 30 |
| active_order_days | 5 | 30 |
| order_lines_30d | 5 | 191 |
| **Segment** | **TAIL** ❌ | **TOP** ✅ |

---

## 部署步骤

```bash
# 1. 上传 loader.py 到服务器
scp data/loader.py root@<SERVER>:/opt/inv-allocation/data/loader.py

# 2. 重启服务
systemctl restart inv-allocation

# 3. 强制 sales 缓存过期，触发全量 Athena 重跑（在服务器上执行）
cd /opt/inv-allocation
sqlite3 results/sfc_cache.db "DELETE FROM sales; DELETE FROM meta WHERE table_name='sales';"
rm -f results/athena_cache.csv results/athena_cache_ts.txt
nohup .venv/bin/python3 refresh_dashboard_cache.py > results/refresh.log 2>&1 &

# 4. 如需同时重建 FC→SFC 任务列表（segment 改变后旧任务需清空）
sqlite3 results/sfc_cache.db "DELETE FROM fc2sfc_tasks; DELETE FROM fc2sfc_triggers; DELETE FROM meta WHERE table_name IN ('fc2sfc_tasks','fc2sfc_triggers');"
```

---

## 注意事项

1. **Athena 查询时间变长**：新 SQL 跨 3 张大表，全量约 90~120 秒。
2. **Sales TTL 12 小时**：SQLite `sales` 表每 12 小时自动更新一次。需立即生效时按上方步骤清空。
3. **FC→SFC 任务列表 TTL 1 小时**：segment 改变后需手动清空 `fc2sfc_tasks`，或等 1 小时自动重建。
4. **快照写入窗口抖动（已知现象，无需修复）**：`wh_inventory_transaction_by_day` 每天约 03:07~03:24 ET 写入前一天快照。若 refresh 在此窗口内运行，`inv_30` 滚动窗口会把最老一天挤出，导致部分边界 SKU 的 `order_lines_30d` 短暂降低，segment 可能出现 100~300 个 SKU 的抖动（TOP↔MIDDLE）。**Crontab 运行点（UTC 整点/半点）已自然避开该窗口（07:00 UTC 在窗口前，07:30 UTC 在窗口后），正常调度不受影响。手动触发 refresh 时，避开 03:07~03:24 ET 即可。**
