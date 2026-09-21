# NJ Inventory Allocation

NJ 仓库库存分配系统 — 根据销售分层和 D2S 决策 FC/SFC 之间的调拨方向。

---

## 项目结构

```
NJ_Inventory_Allocation/
│
├── shared/                      ← 核心共享模块（所有版本调用）
│   ├── inventory.py             ← 库存基数：按 zone_id 区分 FC vs SFC
│   ├── athena.py                ← Athena 连接 + active_days 查询
│   ├── allocator_base.py        ← D2S + Active Days 决策引擎
│   └── __init__.py
│
├── v1_segment/                  ← 归档：nj_segment 文字分层（含大量 TBD）
├── v2_sales_tier/               ← 当前主版本：WMS group_tier 数字分层
│   ├── run_allocation.py        ← 入口，调用 shared 模块
│   ├── allocator.py             ← V2 专用 MODES 定义（group_tier 9-16）
│   ├── LOGIC.md                 ← V2 逻辑文档
│   └── allocation_result.csv   ← 上次运行结果
│
├── v3_segment/                  ← 开发中：新 segment 方案
│   └── run_allocation.py        ← 骨架，填入 MODES + load_segment() 即可运行
│
├── explore/                     ← 数据探索脚本（归档，不参与主流程）
│
├── .env                         ← 凭证（不进 git）
├── .env.example                 ← 凭证模板
└── requirements.txt
```

---

## 核心逻辑（shared 模块）

### 库存基数 (`shared/inventory.py`)

按 zone_id 区分 FC 和 SFC：

| Zone | 含义 | location_type |
|------|------|--------------|
| zone_id = 1 | FC 传统 Bin Pick 区 | 3 (STOCK), 4 (BIN Pick) |
| zone_id = 2 | FC S 区（2026-09 迁入）| 4 (BIN Pick) |
| zone_id = 0 | SFC 存储区 | 3 (STOCK) |

### 分配决策 (`shared/allocator_base.py`)

```
对每个 SKU：
  1. segment_key → MODES 字典 → (mode, D2S上限, D2S下限, 补回触发)
  2. active_days < 5         → TRANSFER_TO_SFC（全迁）
  3. fc_d2s > D2S上限        → TRANSFER_TO_SFC（迁至上限）
  4. fc_d2s < 补回触发        → RETURN_FROM_SFC（补至上限）
  5. 其余                    → KEEP
```

---

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 运行 V2（WMS group_tier 分层）
cd v2_sales_tier
python run_allocation.py

# 运行 V3（新 segment 分层，开发中）
cd v3_segment
python run_allocation.py
```

---

## 新增方案说明

要新增一个分层方案（如 V4），只需：

1. 复制 `v3_segment/` 文件夹改名
2. 在 `run_allocation.py` 里：
   - 实现 `load_segment()` — 从数据库拉你的 segment 数据
   - 填写 `MODES` 字典 — `{segment_int: (mode, upper, lower, return_trigger)}`
3. `shared/` 里的所有代码完全不用动

库存基数永远调用 `load_fc_sfc_inventory()`，逻辑不变。
