# V4 Allocation — 设计文档

> 基于 v3 FC Segment 雏形，重新设计三条核心转移流。
> 区域定义与 v3 保持一致：FC（非 S%/DC% 前缀）vs SFC（S% + DC% 前缀）。

---

## Glossary

| 术语 | 含义 |
|------|------|
| **FC** | Fulfillment Center 区 — `location_no` 不以 `S` 或 `DC` 开头的仓储位（含 Bin Box `K%` 和标准货架） |
| **SFC** | Slow-Moving / DC 区 — `location_no` 以 `S` 或 `DC` 开头的仓储位 |
| **Bin location** | `location_type = 4`，整箱格口位，不拆箱 |
| **Stock location** | `location_type = 3`，散货货架位，按箱规取整 |
| **ADL** | Average Daily Orders = `order_lines_30d / 30` |
| **D2S** | Days-to-Sell = `fc_qty / adl`；`adl = 0` 时为 NaN |
| **AOD** | Active Order Days — 过去 30 天中有下单的天数 |
| **OL** | Order Lines（30 天总行数） |
| **EXP trigger** | `need_minimal_exp = 1`，SKU 有到期日需 FIFO 管理 |
| **exp_date** | 仓储批次到期日 |
| **inv_age** | 库龄（天数），用于 TOP SFC→FC P1-P3 tiebreaker。来源：`wh_lot_master.receive_time`（Unix timestamp）。LEGACY lot（`lot_id = 1`）fallback 到 `wh_inbound_batch.in_dtm`（取该 SKU 最早收货时间）。SQL: `DATEDIFF(CURDATE(), DATE(FROM_UNIXTIME(CASE WHEN lm.lot_id = 1 THEN ib.in_dtm ELSE lm.receive_time END)))` |
| **case_qty** | SKU 标准箱规，用于 Stock 位取整。来源：`yamibuy_im.im_item_extend.piece_pack`（仓库规格），NULL 或 0 时兜底为 1 |
| **excess_vol** | MIDDLE FC→SFC SKU 的超储体积 = `excess_qty × unit_volume_cbm` |
| **needed** | MIDDLE SFC→FC 需补量 = `ceil(adl × 28) - fc_qty` |
| **Classifier** | SKU 分段模块（TOP / MIDDLE / TAIL） |
| **FC_to_SFC_Engine** | FC→SFC 转移计划生成模块 |
| **SFC_to_FC_Engine** | SFC→FC 转移计划生成模块 |
| **MIDDLE_Router** | MIDDLE SFC→FC SKU 路由子模块（T1/T2/T3/T4） |
| **Location_Selector** | 具体库位选取与数量计算子模块 |
| **Queue_Builder** | SKU 优先队列排序子模块 |
| **Putaway_Engine** | 入库分拨决策模块（FC / SFC） |
| **Task_Pickup** | 操作员领取任务阶段，取前 N 个 SKU 后按 `location_no` 重排 |
| **Data_Loader** | 数据加载模块（MySQL 库存 + Athena 销量，含磁盘缓存回退） |
| **Streamlit_App** | Streamlit Web UI |
| **source_type** | Task List 内每行的来源标识，区分同一 list 内不同触发路径的 SKU（`top_emergency` / `top_routine` / `mid_emergency` / `mid_routine` / `mid_zone_fallback` / `mid_bin_to_stock` / `mid_stock_to_fc`） |
| **T1** | 紧急补货 Task List（SFC→FC，fc_d2s < 3 或 Zone Picking 兜底） |
| **T2** | 常规补货 Task List（SFC→FC，fc_d2s 3~28） |
| **T3** | SFC Bin → SFC Stock 移位 Task List |
| **T4** | SFC Stock → FC 物理搬（DateSwitch）Task List |

---

## 0. 版本说明

v4 变更点：
- TAIL 阈值默认 OL < 8（可调参数，与 v3 一致，之前曾改为4已撤回）
- FC→SFC location 选取逻辑全新（v3 逻辑替换），Single Loc 统一 Keep in FC
- SFC→FC 新增四个 Task List（T1/T2/T3/T4），替换旧三个 List
- Putaway 全新（v3 没有）
- 通用规则：SFC 总量不足时全部转移，不卡量

---

## 1. SKU 分类

> **⚠️ 更新 2026-10-01：`ol_tail` 默认值改回 8（可调参数）**

| 档位   | 条件                              | 默认阈值         |
|--------|-----------------------------------|------------------|
| TOP    | AOD ≥ aod_top AND OL ≥ ol_top     | AOD≥21, OL≥30    |
| TAIL   | OL < ol_tail                      | OL < **8**       |
| MIDDLE | 其余                              | —                |

```python
def classify(r):
    if r.active_order_days >= AOD_TOP and r.order_lines_30d >= OL_TOP:
        return "TOP"
    if r.order_lines_30d < OL_TAIL:   # 默认 8，可调
        return "TAIL"
    return "MIDDLE"
```

---

## 2. FC → SFC

> **⚠️ 更新 2026-10-01**

### 2.1 触发条件

| 档位   | 触发                  | 转移量                                      |
|--------|-----------------------|---------------------------------------------|
| MIDDLE | fc_d2s > mid_evict_d2s（默认30） | excess = fc_qty - ceil(adl × mid_keep_d2s)（默认30） |
| TAIL   | fc_qty > 0            | fc_qty 全量（清仓）                         |

> TOP SKU 不参与 FC→SFC 转出。

### 2.2 MIDDLE Location 选取

目标：**留够 `mid_keep_d2s`（默认30） D2S，剩余全部转 SFC**。

**Single Location 规则：** MIDDLE SKU 只有一个 FC 库位时，**不产生转移任务（Keep in FC）**。

```
Single Loc?
├── YES → Keep In FC（不转）
└── NO →
    Has EXP? (need_minimal_exp = 1)
    │
    ├── NO →
    │   1. Sort by Bin first then Stock，同类型内 qty DESC（最大到最小）
    │   2. 从上往下累积 qty 直到 >= ceil(adl × mid_keep_d2s)
    │      最后一个库位：ceiling rule（整批保留，即使超过目标）
    │   3. 累积到的库位：留 FC；剩余库位：转 SFC
    │
    └── YES →
        Single Loc?
        ├── YES → Keep In FC（不转）
        └── NO →
            1. Sort by EXP date DESC（最远到期排前，最好日期先留）
            2. 从上往下累积 qty 直到 >= ceil(adl × mid_keep_d2s)
               最后一个库位：ceiling rule（整批保留）
            3. 累积到的库位：留 FC；剩余库位：转 SFC
```

> **Ceiling rule（向上取整保留）：** 累积到最后一个刚好满足目标的库位时，整批留下不拆分，即使留下后超过 30 D2S 目标。保证 FC 库存完整，不产生零散拆箱。

### 2.3 TAIL Location 选取

所有 FC 库位全部转移，按 `location_no` ASC 排列生成作业清单。

### 2.4 SKU 队列优先级（两阶段）

**阶段一（生成全队列）：**
- MIDDLE：`excess_vol` DESC（超储体积越大越先）→ `avg_daily_orders` DESC（tiebreaker）
- TAIL：`fc_qty` DESC（库存越多越先）

**阶段二（任务领取时）：**
- 取前 N 个 SKU → 在这 N 个内按 `location_no` ASC 重新排序输出

### 2.5 参数

| 参数 | 默认值 | 用途 |
|---|---|---|
| `mid_evict_d2s` | 30 | MIDDLE FC→SFC 触发线（fc_d2s > 此值才转） |
| `mid_keep_d2s` | 30 | MIDDLE FC 保留目标 D2S |
| `ol_tail` | 8 | TAIL 分类阈值（OL < 此值为 TAIL，可调） |

---

## 3. SFC → FC

> **⚠️ 最新版本：2026-10-01（逐条逻辑确认版）**
> 本节已根据最新流程图逐条确认完整重写。旧版（三个 List）已废弃，替换为下方四个 Task List 设计。

### 3.1 通用规则

- **TAIL SKU 的 SFC 存量不参与 SFC→FC 转移**，策略上通过 FC→SFC 全量清仓推至 SFC 消化。
- 每个 Task List 的输出行均带 `segment`（TOP / MIDDLE）和 `source_type` 字段，方便现场区分来源。**segment 不参与排序逻辑，仅作标识。**
- **任务领取时：** 取前 N 个 SKU，在这 N 个内按 `location_no` ASC 重新排序输出。未完成任务滚入下一个 task list。

---

### 3.2 EXP 比较方式（关键定义）

> **SFC 库位的 `exp_date`** 与 **FC 该 SKU 所有批次中最早到期的那一批（`MIN(fc_exp_date)`）** 比较。
>
> `sfc_lot_exp_date < MIN(fc_exp_date)` → SFC 这个库位比 FC 最早的那批**更早到期（更差）**

---

### 3.3 TOP 路由

TOP SKU 无论何种情况，SFC 库存**全部转回 FC**（全量）。没有 T3。

```
TOP，sfc_qty > 0
│
├── fc_d2s < 3
│   → T1（top_emergency，全量）
│
├── fc_d2s ≥ 3，无 EXP trigger 或 sfc_lot_exp >= MIN(fc_exp)
│   → T2（top_routine，全量）
│
└── fc_d2s ≥ 3，EXP trigger，sfc_lot_exp < MIN(fc_exp)（SFC 更差）
    → T4（top_stock_to_fc，全量，按 exp_date ASC → location_no ASC）
```

---

### 3.4 MIDDLE 路由决策树（六条，逐条确认）

入口条件：`sfc_qty > 0`

```
MIDDLE
│
├── 条件1：fc_d2s < 3
│   → T1（mid_emergency）
│   位置：任意（不判断 Bin/Stock）
│   转移量：箱规取整到 mid_target_d2s，先 Bin 后 Stock，qty DESC
│
├── 条件2：EXP trigger，sfc_lot_exp < MIN(fc_exp)（SFC 更差），SFC Bin
│   → T1（mid_zone_fallback）
│   转移量：全量 Bin 位
│
├── 条件3：SFC Bin，满足以下任一：
│   - need_minimal_exp=1 AND sfc_lot_exp >= MIN(fc_exp)（SFC 更新鲜）
│   - need_minimal_exp=0（不需保质期管理）
│   → T3（mid_bin_to_stock，SFC Bin → SFC Stock）
│   转移量：全量 Bin 位
│
├── 条件4：EXP trigger，sfc_lot_exp < MIN(fc_exp)（SFC 更差），SFC Stock
│   → T4（mid_stock_to_fc，SFC Stock → FC）
│   转移量：全部该保质期条件的 Stock 库位，全量
│
├── 条件5：SFC Stock，fc_d2s < 14，
│   need_minimal_exp=0 OR sfc_lot_exp >= MIN(fc_exp)（SFC 更新鲜或相等）
│   → T2（mid_routine）
│   转移量：箱规取整到 mid_target_d2s，Stock qty DESC
│
└── 条件6：SFC Stock，fc_d2s ≥ 14，无 EXP trigger 或 sfc_lot_exp >= MIN(fc_exp)
    → Keep（不生成 task list，标记 source_type="keep"）
```

---

### 3.5 四个 Task List 汇总

| List | source_type | segment | 动作 | 转移量 |
|---|---|---|---|---|
| **T1** | `top_emergency` | TOP | SFC→FC | 全量 |
| **T1** | `mid_emergency` | MIDDLE | SFC→FC | 箱规取整，先 Bin 后 Stock，qty DESC |
| **T1** | `mid_zone_fallback` | MIDDLE | SFC Bin→FC | 全量 Bin |
| **T2** | `top_routine` | TOP | SFC→FC | 全量 |
| **T2** | `mid_routine` | MIDDLE | SFC Stock→FC | 箱规取整，Stock qty DESC |
| **T3** | `mid_bin_to_stock` | MIDDLE | SFC Bin→SFC Stock | 全量 Bin |
| **T4** | `top_stock_to_fc` | TOP | SFC→FC | 全量，exp_date ASC→location_no ASC |
| **T4** | `mid_stock_to_fc` | MIDDLE | SFC Stock→FC | 全部该保质期库位，全量 |
| — | `keep` | MIDDLE | 不转移 | — |

---

### 3.6 Task List 内 SKU 排序

| List | 排序规则 |
|---|---|
| T1 | `order_lines_30d` DESC → `avg_daily_orders` DESC → `location_no` ASC |
| T2 TOP | `order_lines_30d` DESC → `avg_daily_orders` DESC → `location_no` ASC |
| T2 MIDDLE | `fc_d2s` ASC → `batch_volume` DESC（`needed × unit_volume_cbm`）→ `avg_daily_orders` DESC → `location_no` ASC |
| T3 | `exp_date` ASC → `location_no` ASC |
| T4 | `exp_date` ASC → `location_no` ASC |

---

### 3.7 转移量计算规则

#### TOP（全量）
```
transfer_qty = sfc_qty（该 SKU 所有 SFC 库位全量）
```

#### MIDDLE 箱规取整（T1 mid_emergency / T2 mid_routine）
```
needed = ceil(adl × mid_target_d2s) - fc_qty   # mid_target_d2s 默认 28，可调

若 sfc_qty < needed → 全部转移

否则：
  排序：先 Bin 后 Stock（T1），或仅 Stock（T2），同类型内 qty DESC
  逐 lot 累积：
    前面的 lot（未达 needed）：整 lot 全量取
    最后一个 lot（累积刚好 meet needed）：
      qty_to_transfer = ceil(remaining_needed / case_qty) × case_qty
      若 lot_qty < 一整箱 → 取整个 lot
```

#### MIDDLE T3（全量 Bin）
```
transfer_qty = lot_qty（整个 Bin 位全量移到 SFC Stock）
```

#### MIDDLE T4（全部该保质期库位）
```
选出所有 sfc_lot_exp < MIN(fc_exp) 的 Stock 库位 → 全量转 FC
```

---

### 3.8 参数

| 参数 | 默认值 | 用途 |
|---|---|---|
| `mid_target_d2s` | 28 | MIDDLE 目标 D2S（箱规取整目标，可调） |
| `mid_emergency_d2s` | 3 | T1 紧急触发线（fc_d2s < 3） |
| `mid_replen_d2s` | 14 | T2 MIDDLE 常规触发线（fc_d2s < 14） |

---

### 3.6 参数

| 参数 | 默认值 | 用途 |
|---|---|---|
| `top_trigger_d2s` | 28 | TOP 入口 D2S 上限（fc_d2s < 28 才补） |
| `mid_routine_d2s` | 28 | MIDDLE 入口 D2S 上限 |
| `mid_emergency_d2s` | 3 | T1 紧急触发线（fc_d2s < 3） |
| `mid_replen_d2s` | 14 | T2 常规触发线（fc_d2s < 14） |

---

## 4. Putaway（入库分拨）

> **⚠️ 最新版本：2026-10-01（逐条确认版）**

新货到仓时的一次性分拨决策。

### 4.1 决策树

```
Receiving
│
├── TOP → FC
│
├── BOTTOM/TAIL → SFC Stock
│
└── MIDDLE →
    need_exp=1 AND new_exp < MAX(fc_exp)（新货比 FC 最好的还差）?
    ├── YES → FC（新货更差，先进 FC 先出，FIFO）
    └── NO（无 EXP 管理，或新货 >= FC 最好那批）→
        FC > 28 D2S?
        ├── YES → SFC Stock（FC 已够，新货入 SFC）
        └── NO（FC ≤ 28 D2S）→
            SFC has stock?
            ├── YES → SFC Stock（SFC 有货，等 T2 补回来）
            └── NO  → FC（SFC 也空，直接补 FC）
```

### 4.2 决策表

| 条件 | 结果 | putaway_rule |
|---|---|---|
| TOP | FC | `top_fc` |
| BOTTOM/TAIL | SFC Stock | `tail_sfc` |
| MIDDLE，need_exp=1，`new_exp < MIN(fc_exp)`（新货比FC最老批次还要早到期） | FC | `mid_exp_earlier_fc` |
| MIDDLE，无 EXP trigger，或 `new_exp >= MAX(fc_exp)` | 走 D2S 判断 | — |
| MIDDLE，无 EXP trigger，FC > 28 D2S | SFC Stock | `mid_fc_sufficient` |
| MIDDLE，无 EXP trigger，FC ≤ 28 D2S，SFC 有货 | SFC Stock | `mid_sfc_has_stock` |
| MIDDLE，无 EXP trigger，FC ≤ 28 D2S，SFC 空 | FC | `mid_sfc_empty` |

### 4.3 EXP 比较

> 新货的 `new_exp_date` 与 **FC 该 SKU 所有批次中最早到期的那一批（`MIN(fc_exp_date)`）** 比较。
> `new_exp < MIN(fc_exp)` → 新货比 FC 最老那批**还要早到期** → 进 FC 先出（FEFO）
> `new_exp >= MIN(fc_exp)` → 新货不比 FC 最老批次更差 → 走 D2S 判断流程

### 4.4 输出字段

| 字段 | 说明 |
|---|---|
| `putaway_zone` | FC / SFC |
| `putaway_rule` | 决策路径标签（审计用） |
| `putaway_reason` | 人读描述 |

### 4.5 参数

| 参数 | 默认值 | 用途 |
|---|---|---|
| `putaway_d2s` | 28 | MIDDLE 判断 FC 是否已够的 D2S 阈值 |

---

## 5. 参数汇总

> **⚠️ 更新 2026-10-01**

| 参数 | 默认值 | 用途 |
|---|---|---|
| `aod_top` | 21 | TOP 分类 AOD 阈值 |
| `ol_top` | 30 | TOP 分类 OL 阈值 |
| `ol_tail` | **8** | TAIL 分类 OL 上限（可调） |
| `mid_evict_d2s` | 30 | MIDDLE FC→SFC 触发线（FC D2S 超过此值则清出） |
| `mid_keep_d2s` | 30 | MIDDLE FC→SFC 保留目标 D2S |
| `mid_target_d2s` | 28 | MIDDLE SFC→FC 补货目标 D2S（可调） |
| `mid_emergency_d2s` | 3 | T1 紧急触发线（fc_d2s < 此值） |
| `mid_replen_d2s` | 14 | T2 MIDDLE 常规触发线（fc_d2s < 此值） |
| `min_loc_qty` | 5 | 单库位最小转移量阈值（低于此值的库位跳过） |
| `putaway_d2s` | 28 | Putaway MIDDLE 判断 FC 是否已够的 D2S 阈值 |

---

## 6. 输出 Task Lists 汇总

> **⚠️ 最新版本：2026-10-01**

| Task List | 方向 | 触发来源 | source_type |
|-----------|------|---------|-------------|
| FC→SFC TAIL list | FC→SFC | TAIL fc_qty > 0，全量清仓 | — |
| FC→SFC MIDDLE list | FC→SFC | MIDDLE fc_d2s > 30，超储清出 | — |
| **T1 紧急补货** | SFC→FC | TOP fc_d2s < 3；MIDDLE fc_d2s < 3；MIDDLE Zone Picking 兜底（SFC Bin，EXP 更差） | `top_emergency` / `mid_emergency` / `mid_zone_fallback` |
| **T2 常规补货** | SFC→FC | TOP fc_d2s ≥ 3；MIDDLE fc_d2s 3~14，SFC Stock，无 EXP trigger | `top_routine` / `mid_routine` |
| **T3 SFC Bin→SFC Stock** | SFC 内移位 | MIDDLE SFC Bin 位（有 EXP 且 SFC 更新鲜；或无 EXP trigger） | `mid_bin_to_stock` |
| **T4 SFC Stock→FC** | SFC→FC | MIDDLE SFC Stock，EXP trigger，SFC 更新鲜 | `mid_stock_to_fc` |
| Putaway decision | Inbound | 新货入库分拨 FC/SFC | — |

---

## 7. 模块文件规划

```
v4_DC_allocation/
├── app.py                  # Streamlit UI（从 v3 复制修改）
├── config.py               # 参数 P dict
├── data/
│   └── loader.py           # MySQL/Athena 数据加载
├── logic/
│   ├── classify.py         # SKU 分类
│   ├── fc_to_sfc.py        # FC→SFC（MIDDLE evict + TAIL clear）
│   ├── sfc_to_fc.py        # SFC→FC（TOP + MIDDLE 三 list）
│   └── putaway.py          # Putaway 入库分拨
├── planning/
│   ├── ideal.py            # 理想状态计算
│   └── schedule.py         # 30 天滚动排程
├── docs/
│   └── design.md           # 本文件
└── results/
```

---

## 8. 实现顺序

1. `data/loader.py` — 复用 v3 查询，确认数据字段
2. `logic/classify.py` — 复用 v3，改 ol_tail = 4
3. `logic/fc_to_sfc.py` — 新 location 选取逻辑（留够 30 D2S，Bin/Stock 排序）
4. `logic/sfc_to_fc.py` — TOP 两层优先级 + MIDDLE 三 list 路径判断
5. `logic/putaway.py` — 全新，入库决策树
6. `app.py` — UI 从 v3 复制，按新模块替换

---

## 9. 数据加载设计

### 9.1 MySQL — 库存数据

**数据源：** `wh_storage_location_item` JOIN `wh_inventory_transaction`

**过滤条件：**
- `warehouse_no = '002'`
- `location_type IN (3, 4)`（Stock 和 Bin 位）
- `size_id NOT IN (13, 33)`
- `business_type != 8`

**聚合规则：**
- `fc_qty`：`location_no` 不以 `S` 或 `DC` 开头的库存总量
- `sfc_qty`：`location_no` 以 `S` 或 `DC` 开头的库存总量

> **注意：** 不能用 `zone_id = 2` 替代 SFC 过滤。验证发现 `zone_id = 2` 只覆盖 `S%` 前缀库位；所有 `DC%` 前缀库位的 `zone_id = 0`。完整的 SFC 过滤条件必须是 `location_no LIKE 'S%' OR location_no LIKE 'DC%'`，两个条件缺一不可。

**缓存：** 结果缓存 30 分钟；MySQL 连接失败时 UI 展示错误消息，不中断应用。

### 9.2 Athena — 销量数据

**数据源：** `yamibuy_inventory.inventory_record` JOIN `dwb_trade.trade_item_sale_agg_daily`

**输出字段：** `active_order_days`, `order_lines_30d`, `avg_daily_orders`, `sales_units_30d`（per SKU）

**缓存策略：**

| 场景 | 行为 |
|------|------|
| 磁盘缓存存在且 < 12 小时 | 直接读 `results/athena_cache.csv` |
| 缓存缺失或 > 12 小时 | 重新查询 Athena，刷新缓存 |
| Athena 不可用，无有效缓存 | 回退到 `results/segment_sku_detail_*.csv` |
| 两者均不可用 | UI 抛出错误 |

### 9.3 MySQL — 体积与批次数据

| 查询 | 数据源 | 字段 |
|------|--------|------|
| SKU 体积 | `im_item` JOIN `xysc_wearhouse_goods` | `unit_volume_cbm` per SKU |
| FC 批次位数据 | `wh_lot_inventory` JOIN `wh_storage_location` / `wh_lot_master` / `wh_inbound_batch` / `im_item_extend` | `location_no`, `loc_type`, `qty`, `exp_date`, `need_minimal_exp`, `case_qty`（= `piece_pack`，NULL 时默认 1），`inv_age`（见下方注释） |
| SFC 批次位数据 | 同上，过滤 `location_no LIKE 'S%' OR LIKE 'DC%'` | 同上 |

> **`inv_age` 计算逻辑：**
> ```sql
> DATEDIFF(CURDATE(), DATE(FROM_UNIXTIME(
>     CASE
>         WHEN lm.lot_id = 1 THEN ib.in_dtm   -- LEGACY lot: fallback 到最早收货时间
>         ELSE lm.receive_time                  -- 正常 lot: lot tracking 入库时间
>     END
> ))) AS inv_age
> ```
> LEGACY lot 通过 JOIN `wh_inbound_batch`（按 `item_number` 匹配，取 `MIN(in_dtm)`）获得 fallback 时间。

SKU 级别的 `loc_count`、`total_vol` 统计缓存 30 分钟。

---

## 10. Streamlit UI 设计

### 10.1 Segment Dashboard 标签页

- TOP / MIDDLE / TAIL 三个 Metric Card，每张展示：SKU 数量、占总 SKU 百分比、FC 总量、SFC 总量
- 各分段的 FC D2S 平均值与分布
- FC Location Balance 面板：总库位数、可用容量、已占库位、空闲库位、空闲体积（FC 区 / SFC 区分别展示）

### 10.2 Threshold Simulator 标签页

- `aod_top`、`ol_top`、`ol_tail` 三个交互 Slider
- 初始值：`aod_top = 21`，`ol_top = 30`，`ol_tail = 4`
- Slider 调整后实时重算分段，更新 Metric Card，**无需整页刷新**
- 更新仅影响运行时状态，不写入 `config.py`

### 10.3 Transfer Plan 标签页

子标签结构：

| 子标签 | 内容 |
|--------|------|
| FC→SFC | MIDDLE evict list + TAIL clear list |
| SFC→FC | TOP list + MIDDLE List 1 + List 2 + List 3 + Dateswitch（TBD） |
| Putaway | 入库分拨决策列表 |

每个子标签展示：
- 可排序数据表，至少包含：`item_number`, `location_no`, `loc_type`, `qty_to_transfer`, `rule`
- CSV 下载按钮
- 任务数为 0 时展示提示信息，不显示空表

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Classification Exhaustiveness and Mutual Exclusion

*For any* SKU with finite `active_order_days` and `order_lines_30d` values, the Classifier SHALL assign exactly one segment label (TOP, MIDDLE, or TAIL), and no SKU shall be left unclassified.

**Validates: §1（SKU 分类）条件 1、4、5**

### Property 2: Classification Boundary Correctness

*For any* `(aod, ol, aod_top, ol_top, ol_tail)` tuple where `ol >= ol_tail` and `(aod < aod_top OR ol < ol_top)`, the Classifier SHALL assign MIDDLE — ensuring TOP and TAIL boundaries do not overlap into the MIDDLE region.

**Validates: §1 条件 2、3、4**

### Property 3: FC→SFC MIDDLE Kept Quantity Invariant

*For any* MIDDLE SKU with `fc_d2s > 30` and more than one FC location, after location selection the sum of quantities in the "keep in FC" set SHALL be `>= ceil(adl × 30)`, regardless of whether EXP sorting or Bin/Stock sorting is applied.

**Validates: §2.2（MIDDLE Location 选取）**

### Property 4: FC→SFC TAIL Full-Clear Invariant

*For any* TAIL SKU with `fc_qty > 0`, the sum of `qty_to_transfer` across all selected TAIL FC locations SHALL equal `fc_qty`.

**Validates: §2.1（TAIL 触发条件）、§2.3（TAIL Location 选取）**

### Property 5: MIDDLE SKU Queue Sort Ordering

*For any* set of MIDDLE FC→SFC SKUs, the Queue_Builder output SHALL be ordered such that no SKU with a lower `excess_vol` appears before a SKU with a higher `excess_vol`; ties among equal `excess_vol` values SHALL be resolved by `avg_daily_orders` DESC.

**Validates: §2.4（SKU 队列优先级）**

### Property 6: TOP SFC→FC Priority Ordering (P0 Before P1-P3)

*For any* set of TOP SFC→FC SKUs containing at least one P0 SKU (`fc_d2s < 3`) and at least one non-P0 SKU, the Queue_Builder output SHALL place all P0 SKUs before all non-P0 SKUs.

**Validates: §3.2（TOP SFC→FC SKU 队列优先级）**

### Property 7: MIDDLE Router Exhaustiveness

*For any* valid MIDDLE SFC→FC SKU input (with defined `fc_d2s`, EXP flag, and SFC location type), the MIDDLE_Router SHALL produce exactly one routing outcome from the set {List 1, List 2, List 3, Dateswitch, Keep at SFC}.

**Validates: §3.3（MIDDLE SFC→FC 三个 List 路径判断）**

### Property 8: MIDDLE SFC→FC Transfer Quantity Sufficiency

*For any* MIDDLE SFC→FC SKU where `sfc_qty >= needed`, the sum of `qty_to_transfer` across all selected SFC locations SHALL be `>= needed`.

**Validates: §3.4（MIDDLE Location 选取 SFC→FC）**

### Property 9: Universal Rule — Transfer All When SFC Insufficient

*For any* SFC→FC transfer where `sfc_qty < needed`, the total `qty_to_transfer` SHALL equal `sfc_qty` (all available SFC inventory is transferred, not capped to `needed`).

**Validates: §3.1（通用规则）**

### Property 10: Stock Location Case-Rounding Invariant

*For any* SFC Stock location selected for MIDDLE SFC→FC transfer, `qty_to_transfer` SHALL be a positive integer multiple of `case_qty` (rounded up, never down, never partial case).

**Validates: §3.4（箱规取整）**

### Property 11: Putaway Decision Tree Exhaustiveness

*For any* valid inbound SKU with a defined segment, `fc_d2s`, `sfc_qty`, and optional `exp_date`, the Putaway_Engine SHALL assign exactly one `putaway_zone` value from {FC, SFC}.

**Validates: §4（Putaway 入库分拨）**

### Property 12: Task Pickup Location-No Re-sort

*For any* top-N SKU subset taken from any task queue (FC→SFC, SFC→FC TOP, or MIDDLE Lists 1-3), the final output task list SHALL be sorted by `location_no` ASC, overriding the priority-queue ordering used for SKU selection.

**Validates: §2.4、§3.2、§3.3（任务领取时 re-sort）**

---

## Testing Strategy

### Dual Approach: Property-Based + Example-Based

- **Property-based tests** (pytest + Hypothesis, minimum 100 iterations): validate Properties 1–12
- **Example-based unit tests**: validate specific examples, edge cases (single-location MIDDLE, zero-stock SKUs, ADL = 0), and error paths (MySQL failure, Athena fallback)
- **Integration smoke tests**: validate end-to-end data loading (MySQL connection, Athena execution, CSV fallback) with 1–3 representative examples

### Unit Test Focus Areas

- Classification boundary edges (OL = 3, OL = 4, OL = 29, OL = 30; AOD = 20, AOD = 21)
- Single-location MIDDLE SKU produces no FC→SFC task (Requirement 3.3)
- TOP full-transfer when `sfc_qty > 0` (Requirement 5.1)
- MIDDLE routing for all five outcome branches (Requirements 6.2–6.6)
- Putaway EXP comparison branches (Requirements 8.7, 8.8)
- MySQL/Athena error fallback paths (Requirements 9.5, 10.5, 10.6)

### Property Test Configuration

- Minimum 100 iterations per property test
- Each property test tagged: `# Feature: nj-inventory-allocation-v4, Property N: {title}`
- Hypothesis `@given` strategies to generate:
  - Random `(aod, ol)` sales data for classification tests
  - Random lot DataFrames with varied `exp_date`, `qty`, `loc_type` for location selection tests
  - Random SKU DataFrames with varied `fc_d2s`, `sfc_qty`, `adl`, `case_qty` for transfer and putaway tests


---

## 9. 数据库字段验证记录

> 更新日期：2026-10-01
> 验证方式：`SHOW FULL COLUMNS` + 实际查询测试

### 9.1 已验证字段 ✅

| 字段 | 表 | DB | 类型 | 说明 | 用途 |
|---|---|---|---|---|---|
| `location_no` | `wh_storage_location` | yamibuy_wh | varchar(100) | 库位编号 | FC/SFC 区分（S%/DC% 前缀） |
| `location_type` | `wh_storage_location` | yamibuy_wh | int | 3=Stock, 4=Bin | 库位类型判断 |
| `size_id` | `wh_storage_location` | yamibuy_wh | int | 13=测试, 33=冷冻（排除） | 库位过滤 |
| `warehouse_number` | `wh_storage_location` | yamibuy_wh | varchar(20) | 002=NJ仓 | 仓库过滤 |
| `size_id` | `wh_location_size` | yamibuy_wh | int unsigned | PK | JOIN 键 |
| `volume` | `wh_location_size` | yamibuy_wh | bigint | 库位体积 | 容量计算 |
| `fill_rate` | `wh_location_size` | yamibuy_wh | double(11,3) | 填充率 | 可用容量 |
| `storage_type` | `wh_location_size` | yamibuy_wh | varchar(100) | 存储方式 | 展示用 |
| `item_number` | `wh_lot_inventory` | yamibuy_wh | varchar(20) | 商品编号 | JOIN 键 |
| `location_no` | `wh_lot_inventory` | yamibuy_wh | varchar(20) | 库位编号 | 库位选取 |
| `quantity` | `wh_lot_inventory` | yamibuy_wh | int | 可用数量 | 库存量 |
| `lot_id` | `wh_lot_inventory` | yamibuy_wh | bigint | 批次号 | JOIN 键 |
| `lot_id` | `wh_lot_master` | yamibuy_wh | bigint | 批次唯一标识（PK，自增） | JOIN 键 |
| `receive_time` | `wh_lot_master` | yamibuy_wh | int（Unix ts，NOT NULL） | 收货时间戳 | `inv_age` 计算：`DATEDIFF(CURDATE(), DATE(FROM_UNIXTIME(lm.receive_time)))` |
| `source_number` | `wh_lot_master` | yamibuy_wh | varchar(30) | 入库单号（格式：`002xxxxxxx` 或 `Fxxxxxxx`） | ⚠️ 不等于 `inbound_number`，不能用作 JOIN 键 |
| `inbound_number` | `wh_inbound_batch` | yamibuy_wh | varchar(20) | 收货业务编号（格式：`IB24123195`） | ⚠️ 不匹配 `source_number`，不能用 |
| `lot_id` | `wh_inbound_batch` | yamibuy_wh | bigint | 批次号，收货时回写 | ✅ **正确 JOIN 键**：`ib.lot_id = li.lot_id` |
| `qc_ExpiredDate` | `wh_inbound_batch` | yamibuy_wh | int（Unix ts，Nullable） | QC 保质期时间 | `DATE(FROM_UNIXTIME(ib.qc_ExpiredDate))` |
| `in_dtm` | `wh_inbound_batch` | yamibuy_wh | int（Unix ts，Nullable） | 入库时间 | LEGACY lot fallback 用 |
| `pieces_per_pack` | `wh_inbound_batch` | yamibuy_wh | varchar(30) | 商品规格（批次层面） | 箱规备用来源 |
| `item_number` | `im_item` | yamibuy_im | varchar(15) | 商品编码 | JOIN 键 |
| `business_type` | `im_item` | yamibuy_im | tinyint unsigned | 商业类型（!=8 过滤） | SKU 过滤 |
| `goods_id` | `im_item` | yamibuy_im | bigint | 商品 goods ID | JOIN 键 |
| `need_minimal_exp` | `im_item_extend` | yamibuy_im | int（Nullable） | 是否需要保质期管理 | EXP trigger 判断 |
| `pieces_per_pack` | `im_item_extend` | yamibuy_im | varchar(30) | 出厂包装规格 ✅ 用这个做箱规 | `case_qty` 解析 |
| `piece_pack` | `im_item_extend` | yamibuy_im | varchar(100) | 仓库规格（备用，不使用） | — |
| `goods_id` | `xysc_wearhouse_goods` | yamibuy_master | int | PK | JOIN 键 |
| `volume` | `xysc_wearhouse_goods` | yamibuy_master | double | 体积（单位：in³） | `unit_volume_cbm` |
| `item_number` | `wh_storage_location_item` | yamibuy_wh | varchar(15) | 商品编号 | JOIN 键 |
| `quantity` | `wh_inventory_transaction` | yamibuy_wh | int | 总库存 | 库存数量 |

### 9.2 关键 BUG 记录 ⚠️

**`loader.py` 中的 JOIN 错误（待修复）：**

```sql
-- ❌ 现有错误写法（0行匹配，source_number 格式 ≠ inbound_number 格式）
LEFT JOIN yamibuy_wh.wh_inbound_batch ib ON ib.inbound_number = lm.source_number

-- ✅ 正确写法（通过 lot_id 直接关联）
LEFT JOIN yamibuy_wh.wh_inbound_batch ib ON ib.lot_id = li.lot_id
```

`source_number` 格式（`002xxxxxxxxx` 或 `Fxxxxxxxxx`）与 `inbound_number` 格式（`IB24123195`）完全不同，无法 JOIN。正确做法是直接用 `wh_inbound_batch.lot_id = wh_lot_inventory.lot_id`。

**影响范围：** 当前 `load_fc_lot_data()` 和 `load_sfc_lot_data()` 的 `exp_date` 和 `inv_age_days` 全部是 NULL，因为 JOIN 不通。

### 9.3 待验证字段 ⬜

| 字段 | 表 | DB | 验证内容 | 优先级 |
|---|---|---|---|---|
| Athena `order_lines_30d` | `dwb_trade.trade_item_sale_agg_daily` | Athena | 确认字段存在且数值范围合理 | 🟡 中 |
| Athena `active_order_days` | `yamibuy_inventory.inventory_record` | Athena | 确认字段存在且数值范围合理 | 🟡 中 |

### 9.4 已验证补充（2026-10-01 运行 `_diag_fc.py`）

**`ib.lot_id = li.lot_id` JOIN 结果：**
- ✅ **46,191 行** FC lot 有 exp_date 数据
- ✅ `recv_date`（`lm.receive_time`）正常返回
- ✅ `case_qty`（`ib.pieces_per_pack`）正常返回

**`pieces_per_pack` 来源对比（最终确认）：**

| 字段 | 来源 | 可靠性 | 覆盖率 | 决策 |
|---|---|---|---|---|
| `wh_item_setting.spec` | WMS 仓库运营维护，全链路实际使用 | ✅ **最准** | 100% | **主要来源** |
| `wh_inbound_batch.pieces_per_pack` | 批次层面，按批可能不同 | ✅ 准确但按批 | 部分 | 备用参考 |
| `im_item_extend.pieces_per_pack` | 商品侧出厂规格 | ❌ 多处=1，严重偏差 | — | 不用 |
| `im_item_extend.piece_pack` | 仓库规格 | ❌ 多处=1 | — | 不用 |

**SQL 取法（`wh_item_setting.spec`）：**
```sql
LEFT JOIN yamibuy_wh.wh_item_setting wis
    ON wis.item_number = li.item_number
   AND wis.warehouse_number = '002'
...
CASE WHEN wis.spec IS NULL OR wis.spec = '' THEN 1
     ELSE CAST(wis.spec AS UNSIGNED)
END AS case_qty
```

**真实差异示例（说明为何不能用 im_item_extend）：**
- item=1022714901：`wh_spec=15`，`item_ppp=1`（严重错误），`batch_ppp=15`
- item=1023826241：`wh_spec=15`，`item_ppp=1`，`batch_ppp=9`（批次不统一）

### 9.4 验证脚本

验证脚本位于：`c:\Project-Z\NJ_Inventory_Allocation\_diag_fc.py`

运行方式：
```
cd c:\Project-Z\NJ_Inventory_Allocation
python _diag_fc.py
```

---

## 10. Putaway 数据源 — 待上架库存

> 验证日期：2026-10-05  
> 验证方式：直接查询生产 MySQL，测试脚本 `_test_putaway_sql.py`

### 10.1 数据源定义

**待上架（Received, Waiting for Putaway）** = 货物已收进收货暂存位（RECG 前缀），尚未被 Receiving Put Away 移到货架 Stock/Bin 位。

| 要素 | 说明 |
|------|------|
| **物理位置** | `location_no LIKE 'RECG%'`（RECG0001 ~ RECG1000，共 1,000 个暂存位） |
| **location_type** | `1` = RECEIVING_SELLABLE_TOTE（好货待上架暂存位） |
| **等价条件** | `location_type = 1` ↔ `location_no LIKE 'RECG%'`，100% 对应，无例外 |
| **数据表** | `yamibuy_wh.wh_inventory_transaction`（实时库存） |
| **过滤条件** | `warehouse_number = '002'` AND `quantity > 0` AND `sl.location_type = 1` |
| **不含坏货** | `location_type = 2`（RECN 前缀）为 unsellable，默认排除 |

### 10.2 验证结果（2026-10-05）

```
RECG 位数量：   1,000 个（RECG0001 ~ RECG1000）
当前待上架：    96 SKUs / 3,519 units（全部堆在 RECG0001 共用暂存位）
RECG = type=1：100% 对应，无其他前缀
```

### 10.3 SQL（load_pending_putaway）

```sql
-- 批量查询：NJ 当前所有待上架货物
SELECT
    it.item_number,
    it.location_no,
    SUM(it.quantity)               AS waiting_putaway_qty,
    FROM_UNIXTIME(MAX(it.edit_dtm)) AS last_update
FROM yamibuy_wh.wh_inventory_transaction it
JOIN yamibuy_wh.wh_storage_location sl
    ON  sl.warehouse_number = it.warehouse_number
    AND sl.location_no      = it.location_no
WHERE it.warehouse_number = '002'
  AND it.quantity > 0
  AND sl.location_type = 1          -- RECG 收货暂存位
GROUP BY it.item_number, it.location_no
ORDER BY waiting_putaway_qty DESC;
```

**注意：** `edit_dtm` 对部分老批次为 NULL（NaT），不影响 quantity 准确性，查询仍可用。

### 10.4 Putaway 决策所需额外字段

从待上架数据获得 `item_number` 后，需 JOIN 以下数据：

| 字段 | 来源 | 用途 |
|------|------|------|
| `seg` | 现有 classify 逻辑（Athena + 库存） | TOP/MIDDLE/TAIL 判断 |
| `fc_d2s` | 现有 `df` | MIDDLE 是否 FC 充足 |
| `sfc_qty` | 现有 `df` | MIDDLE SFC 是否有货 |
| `need_minimal_exp` | `im_item_extend.need_minimal_exp` | EXP 管理判断 |
| `new_exp_date` | `wh_inbound_batch.qc_ExpiredDate`（当前批次） | EXP 比较 |
| `fc_max_exp_date` | `load_fc_exp_summary` | 与新货 EXP 比较 |

### 10.5 Loader 函数

```python
# data/loader.py — load_pending_putaway()
def load_pending_putaway() -> tuple[pd.DataFrame, str | None]:
    """
    Query NJ warehouse for items received but not yet put away.
    Returns items currently sitting in RECG (location_type=1) staging locations.
    
    Returns (DataFrame, None) on success:
        Columns: item_number, location_no, waiting_putaway_qty, last_update
    Returns (None, error_str) on failure.
    """
```

---
