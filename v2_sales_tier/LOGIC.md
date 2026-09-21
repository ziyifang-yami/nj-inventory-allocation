# V2 — WMS Sales Tier (group_tier) Based Allocation

---

## 数据来源

| 数据 | 来源 | 字段 |
|------|------|------|
| Sales Tier | `yamibuy_wh.wh_lr_product_location` (MySQL) | `group_tier` |
| 日均销量 | 同上 | `average_sales` |
| 销量排名 | 同上 | `sales_tier`（绝对排名），`sale_volume_ranking`（体积排名）|
| FC 库存 | `yamibuy_inventory.inventory_record` wh=001 | available+allocated+... |
| SFC 库存 | 同上 wh=002 | |
| Active Days | `dwb_trade.trade_item_sale_agg_daily` | COUNT(DISTINCT day_id) WHERE orders>0, 30d |
| 日均订单 | 同上 | SUM(orders)/30 |

---

## group_tier 含义（NJ=002 实测，2026-09-10）

| group_tier | SKU数 | 日均销量范围 | sales_tier 范围 | 分配模式 |
|-----------|-------|-----------|---------------|---------|
| 9 | 60 | 94–721/天 | 1–50 | FC_PRIMARY |
| 10 | 241 | 50–112/天 | 51–200 | FC_PRIMARY |
| 11 | 467 | 17–50/天 | 201–450 | FC_PRIMARY |
| 12 | 1,133 | 7–27/天 | 451–950 | DUAL_WH |
| 13 | 4,115 | 2–14/天 | 951–2,450 | DUAL_WH |
| 14 | 14,372 | 0.5–5/天 | 2,451–8,850 | DC_DIRECT |
| 15 | 31,188 | 0.06–1.3/天 | 8,851+ | DC_DIRECT |
| 16 | 203,889 | 极低/无 | 0（无排名）| DC_DIRECT |

> 同一 SKU 可能在多个 group_tier 有记录（不同库位类型）。
> 取 `MIN(group_tier)` 以最高频分组为准。

---

## 分配规则

| group_tier | 模式 | 保留 D2S 区间 | 补回触发 |
|-----------|------|-------------|---------|
| 9, 10, 11 | FC_PRIMARY | 35–45 | <35 |
| 12, 13 | DUAL_WH | 28–35 | <10 |
| 14, 15, 16, 其他 | DC_DIRECT | 14–28 | <7 |

---

## 核心参数

| 参数 | 值 |
|------|-----|
| Active Days 窗口 | 过去 30 天 |
| Active Days 阈值 | ≥ 5 天 |
| D2S 端点 | inclusive |
| 迁出数量目标 | fc_qty 迁到 D2S 上限对应库存 |
| 补回数量目标 | 补到尽量接近 D2S 上限 |

---

## 决策流程

```
对每个 NJ SKU：
  1. 取 group_tier（MIN，is_deleted=0）→ 确定模式
  2. 计算 active_days（过去30天有订单天数）
  3. 计算 fc_d2s = fc_qty / avg_daily_orders(orders_30d / 30)
  4. 判断动作：
     - active_days < 5           → TRANSFER_TO_SFC（全迁）
     - fc_d2s > D2S上限           → TRANSFER_TO_SFC（迁至上限）
     - fc_d2s < 补回触发 AND sfc>0 → RETURN_FROM_SFC（补至上限）
     - 其余                       → KEEP
```
