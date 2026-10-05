# NJ FC Segment — Planning Logic

> 持续更新中。按模块逐步梳理，边对流程边补充。

---

## 1. 数据源

### 1.1 库存快照（实时，MySQL）

**来源表：** `wh_storage_location_item` JOIN `wh_inventory_transaction`

**过滤条件：**
- `warehouse_number = '002'`（NJ 仓）
- `location_type IN (3, 4)`（Bin + Stock 货架库位）
- `size_id NOT IN (13, 33)`（排除测试库位 + 冷冻库位）
- `business_type ≠ 8`（排除特殊品类）
- `quantity IS NOT NULL AND quantity != 0`

**FC / SFC 区分（按 location_no 前缀）：**

| 前缀 | 归属 | 说明 |
|------|------|------|
| `S%` | SFC | Shelf/货架 DC 区 |
| `DC%` | SFC | DC 区 |
| 其他（A/B/C/D/E/F/K...） | FC | 货架区，含 K% Bin Box |

> `K%` 是 Bin Box 货架库位，属于 FC，不归入 SFC。

**输出字段：**
- `fc_qty` — SKU 在 FC 货架的在库数量
- `sfc_qty` — SKU 在 SFC/DC 的在库数量

**缓存：** `@st.cache_data(ttl=1800)`，30 分钟刷新

---

### 1.2 销售数据（Athena 优先，降级 CSV）

**主源：Athena**
- `yamibuy_inventory.inventory_record` — 取近 30 个有库存日（`available_qty > 0, virtual_qty = 0`）
- `dwb_trade.trade_item_sale_agg_daily` — 日级订单销售数据

**输出字段：**
- `active_order_days` — 近 30 天有下单的天数（AOD）
- `order_lines_30d` — 近 30 天总订单行数（OL）
- `avg_daily_orders` — 日均单量（ADL = order_lines_30d / 30）
- `sales_units_30d` — 近 30 天销售件数

**降级：** Athena 失败时回退最新 CSV（`results/segment_sku_detail_*.csv`）

**缓存：** `@st.cache_data(ttl=3600)`，60 分钟刷新

---

### 1.3 体积数据（MySQL）

**来源表：** `im_item` JOIN `xysc_wearhouse_goods`

**输出字段：**
- `unit_volume_cbm` — 单件体积（字段名含 cbm，实为 **in³**）

**用途：** 计算转移体积 = qty × unit_vol，用于体积平衡图表和 pallet 数估算

---

### 1.4 库位统计（MySQL，FC 侧）

**来源表：** `wh_lot_inventory` JOIN `wh_storage_location`

**函数：** `load_sku_loc_stats(tuple)`

**过滤：** FC 库位（排除 `S%` 和 `DC%`），`location_type IN (3, 4)`

**输出字段（per SKU）：**
- `total_vol` — SKU 在 FC 的真实总体积（in³）
- `loc_count` — SKU 占用的 FC 库位数量

**用途：** TAIL / MIDDLE FC→DC 队列的排序依据（体积大的先转）

**缓存：** `@st.cache_data(ttl=1800)`

---

### 1.5 容量数据（MySQL）

**来源表：** `wh_storage_location` JOIN `wh_location_size`

**函数：** `load_capacity()`

**输出字段（FC / SFC 分区）：**
- `total_locs` — 库位总数
- `allowable_cap` — 可用容量（= volume × fill_rate，in³）
- `occupied_locs` — 已占用库位数
- `free_locs` — 空闲库位数（= total_locs − occupied_locs）
- `free_volume` — 空闲体积（= allowable_cap − used_volume，in³）

**用途：** FC Location Balance 面板的初始值，规划结束后算 Net Free

---

### 数据源汇总

```
MySQL (实时)                         Athena / CSV (日级)
─────────────────────────────        ─────────────────────────────
库存快照  → fc_qty, sfc_qty           销售数据  → AOD, OL, ADL
库位统计  → total_vol, loc_count
容量数据  → free_locs, allowable_cap
体积数据  → unit_volume_cbm
```

---

## 2. SKU 分类

### 2.1 数据依赖

使用数据源 **1.2（销售数据）** 中的两个字段：
- `active_order_days` — 近 30 天有下单的天数（AOD）
- `order_lines_30d` — 近 30 天总订单行数（OL）

### 2.2 分类逻辑

优先级从高到低判断，**三档互斥**：

| 档位 | 判断条件 | 默认值 | 含义 | 仓储策略 |
|------|---------|--------|------|---------|
| **TOP** | AOD ≥ aod_top **AND** OL ≥ ol_top | AOD ≥ 21，OL ≥ 30 | 高频活跃 SKU | 优先放 FC，保持足够 D2S |
| **TAIL** | OL < ol_tail | OL < 8 | 低频 / 慢销 SKU | 清空 FC，全移至 SFC/DC |
| **MIDDLE** | 以上都不满足 | — | 中频 SKU | FC + SFC 双仓持有，动态调配 |

**代码实现（`classify` 函数）：**

```python
def classify(r):
    if r.active_order_days >= aod_top and r.order_lines_30d >= ol_top:
        return "TOP"
    if r.order_lines_30d < ol_tail:
        return "TAIL"
    return "MIDDLE"
```

---

## 3. Ideal Allocation 逻辑

### 3.1 TOP — DC→FC 全量转移

**目标：** 所有 TOP SKU 的 DC 库存全部转回 FC。只要触发转移动作，转移量 = 该 SKU 在 DC 的全部数量（`sfc_qty`），不按 D2S 截断。遗留在 DC 会带来后续的 aging、EXP 和库存管理问题。

**触发条件：** `sfc_qty > 0`（DC 有货就转）

**转移量：** `transfer_qty = sfc_qty`（全量）

**SKU 优先级排序**（cap 约束下决定哪个 SKU 先处理）：

| 优先级 | 维度 | 条件 | 说明 |
|--------|------|------|------|
| P1 | EXP trigger | DC exp_date < FC exp_date | DC 库存到期日早于 FC，必须优先移入 FC 销售，防止过期损失 |
| P2 | FC D2S | FC D2S 越低越优先 | FC 补货紧迫，D2S < 28 的 SKU 先处理 |
| P3 | Inv aging | DC inv_date < FC inv_date | DC 库存入库更早，FIFO 原则优先移入 FC |

> 优先级只影响"谁先被安排"，不影响"转多少"——一旦轮到某个 SKU，转移的是其所有 DC 库存。

---

### 3.2 TAIL — FC→DC 全量转移

**目标：** 所有 TAIL SKU 的 FC 库存全部清出，移至 DC/SFC。

**触发条件：** `fc_qty > 0`（FC 有货就转）

**转移量：** `transfer_qty = fc_qty`（全量清仓）

**SKU 优先级排序：** 按 FC 库存量降序（`fc_qty DESC`）——库存越多越先处理，优先释放更多 FC 空间。

---

### 3.3 Location 排序（作业计划输出）

TOP 和 TAIL 在生成给现场的每日作业计划时，SKU 优先级只决定"哪些 SKU 进今天的 list"（按 cap 截取前 N 个 SKU）。

**进入当天 list 后，按 location_no 重新排序输出**，方便现场按区域顺序作业，减少行走路程：

- 主排序：`location_no` 的区域前缀（字母 + 数字段）— 同区域的库位集中操作
- 次排序：`location_no` 升序

> 例：今天 TOP 转移前 100 个 SKU，确定 SKU 列表后，输出时按 A 区 → B 区 → C 区... 的 location 顺序排列，而非 SKU 优先级顺序。

---

## 4. 每日循环算法（待补充）

---

## 5. 输出展示（待补充）
