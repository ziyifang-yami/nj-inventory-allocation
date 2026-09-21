# NJ Inventory Allocation — 分配逻辑文档

> 最后更新：2026-09-10

---

## 核心参数

| 参数 | 值 | 说明 |
|------|-----|------|
| Active Days 窗口 | 过去 30 天 | COUNT(DISTINCT day_id WHERE orders > 0) |
| Active Days 阈值 | ≥ 5 天 | 低于此值视为低频，触发迁出 |
| D2S 端点 | inclusive（≤ / ≥） | 边界值归属到"保留"区间 |
| 迁出数量目标 | D2S 上限 | transfer_qty = current_qty - target_upper_qty |
| 补回数量目标 | 尽量接近 D2S 上限 | return_qty = target_upper_qty - fc_current_qty |
| D2S 计算 | current_qty / (orders_30d / 30) | 分母用 30 天固定，不用 active days |

---

## 分配规则

### Segment → 模式 → D2S 区间

| Segment | 模式 | 保留区间（D2S） | 迁出条件 | 补回触发（FC D2S） |
|---------|------|----------------|---------|-------------------|
| Star / Hit / Head | FC Primary | 35 – 45 | >45 D2S OR <5 active days | <35 D2S AND ≥5 active days |
| Middle | Dual WH | 28 – 35 | >35 D2S OR <5 active days | <10 D2S AND ≥5 active days（补到接近35）|
| Longtail / TBD | DC Direct | 14 – 28 | >28 D2S OR <5 active days | <7 D2S AND ≥5 active days（补到接近28）|

> **补回目标**：统一补到各自 D2S **上限**（FC Primary → 45，Dual WH → 35，DC Direct → 28）

---

## 决策树（每个 SKU）

```
输入: segment, fc_d2s, sfc_qty, active_days_30d, avg_daily_orders

STEP 1: 确定模式
  Star/Hit/Head  → FC_PRIMARY   (上限=45, 下限=35, 补回触发=35)
  Middle         → DUAL_WH      (上限=35, 下限=28, 补回触发=10)
  Longtail/TBD   → DC_DIRECT    (上限=28, 下限=14, 补回触发=7)

STEP 2: active_days < 5?
  YES → 动作 = TRANSFER_TO_SFC
        transfer_qty = fc_current_qty  (全迁，因为不活跃)
        DONE

STEP 3: fc_d2s > 上限?
  YES → 动作 = TRANSFER_TO_SFC
        target_fc_qty = avg_daily_orders × 上限
        transfer_qty = fc_current_qty - target_fc_qty
        DONE

STEP 4: fc_d2s < 补回触发 AND sfc_qty > 0?
  YES → 动作 = RETURN_FROM_SFC
        target_fc_qty = avg_daily_orders × 上限
        return_qty = min(target_fc_qty - fc_current_qty, sfc_qty)
        DONE

STEP 5: 保留区间内 (下限 ≤ fc_d2s ≤ 上限)
  → 动作 = KEEP
```

---

## 输出字段

| 字段 | 说明 |
|------|------|
| `item_number` | SKU |
| `segment` | Star/Hit/Head/Middle/Longtail/TBD |
| `mode` | FC_PRIMARY / DUAL_WH / DC_DIRECT |
| `action` | KEEP / TRANSFER_TO_SFC / RETURN_FROM_SFC |
| `fc_qty` | 当前 FC 库存 |
| `sfc_qty` | 当前 SFC 库存 |
| `fc_d2s` | FC 当前 D2S |
| `active_days` | 过去30天有销售的天数 |
| `avg_daily_orders` | 日均销量（orders_30d / 30） |
| `transfer_qty` | 建议调拨量（正数=FC→SFC，负数=SFC→FC）|
| `target_fc_qty` | 目标 FC 库存量（对应 D2S 上限）|
| `reason` | 触发原因说明 |

---

## 数据来源

| 数据 | 表 | 字段 |
|------|-----|------|
| Segment | `dwb_trade.trade_item_sale_agg_daily` | `nj_segment` |
| FC 库存 | `yamibuy_inventory.inventory_record` (wh=001) | available+allocated+reserved+receive+in_transit |
| SFC 库存 | `yamibuy_inventory.inventory_record` (wh=002) | 同上 |
| Active Days | `dwb_trade.trade_item_sale_agg_daily` | COUNT(DISTINCT day_id) WHERE orders>0, last 30d |
| 日均销量 | 同上 | SUM(orders) / 30 |
