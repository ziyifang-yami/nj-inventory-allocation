# NJ 库存分配系统 — v4

NJ 仓库双仓库存管理系统（FC ↔ SFC），提供实时库存转移计划与上架决策。  
以三个 Streamlit 应用运行在服务器上。

**运行中的应用：**

| 端口 | 文件 | 描述 |
|------|------|------|
| 8508 | `app.py` | 主看板（总览 · 前后对比 · FC→SFC · SFC→FC · 上架） |
| 8512 | `app_test_sfc.py` | SFC→FC 任务列表（测试视图） |
| 8513 | `app_test_fc2sfc.py` | FC→SFC 任务列表（测试视图） |

---

## 项目结构

```
NJ_Inventory_Allocation/
├── app.py                    # 主服务入口（8508，生产）
├── app_test_fc2sfc.py        # FC→SFC 测试服务（8513）
├── app_test_sfc.py           # SFC→FC 测试服务（8512）
├── cache_db.py               # SQLite 缓存封装
├── config.py                 # 阈值配置（aod_top/ol_top/ol_tail 等）
├── refresh_dashboard_cache.py      # 定时缓存刷新（cron 调用）
├── refresh_watchdog.py       # 刷新看门狗
├── requirements.txt
├── .env / .env.example
│
├── data/
│   └── loader.py             # Athena + MySQL 数据加载层 ← 核心，勿随意修改
│
├── logic/
│   ├── classify.py           # SKU 分段逻辑（TOP/MIDDLE/TAIL/TBD）
│   ├── fc_to_sfc.py          # FC→SFC 调拨引擎
│   ├── sfc_to_fc.py          # SFC→FC 补货引擎
│   └── putaway.py            # 上架决策引擎
│
├── tests/                    # 对应 logic/ 的单测 + 属性测试
│
├── docs/
│   ├── design.md             # 系统设计文档
│   ├── LOGIC.md              # 核心逻辑说明
│   ├── ui-layout-guide.md    # UI 布局指南
│   └── hotfix-2026-10-28-data-source-switch.md  # ← 最新热补记录
│
├── results/                  # 运行时缓存（不进 git）
│   ├── sfc_cache.db
│   ├── athena_cache.csv / athena_cache_ts.txt
│   └── *.parquet / *_ts.txt
│
└── scripts/
    ├── _deploy_apps.py       # 部署主应用到服务器
    ├── _deploy_watchdog.py   # 部署看门狗
    ├── _restart_services.py  # 重启服务
    ├── _find_service.py      # 查服务状态
    ├── _ssh_check.py         # SSH 健康检查
    ├── _force_refresh.sh     # 强制刷新缓存
    ├── _check_refresh.sh     # 检查刷新状态
    ├── _srv_check.sh         # 服务器状态检查
    ├── check_cache_age.py    # 检查缓存新鲜度
    ├── check_sku_seg.py      # 检查 SKU segment
    ├── check_athena_history.py # 查 Athena 历史
    │
    ├── _debug_today/         # 当日临时调试脚本（不进 git）
    └── _archive/             # 历史脚本归档
        ├── deploy/           # 旧部署脚本
        ├── check/            # 旧检查脚本
        ├── debug/            # 旧调试脚本
        └── force/            # 旧强制刷新脚本
```

---

## 数据源（2026-10-28 更新）

| 用途 | 表 | 说明 |
|------|-----|------|
| 库存快照 | `yamibuy_wh.wh_inventory_transaction_by_day` | 每日库位库存快照（WH 层） |
| 销售订单 | `yamibuy_wh.wh_order_info` + `wh_order_goods` + `yamibuy_im.im_item` | 完成订单，通过 goods_id join 解决 bundle/combo 问题 |

> 详见 `docs/hotfix-2026-10-28-data-source-switch.md`

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置凭据
cp .env.example .env
# 编辑 .env，填入 MYSQL_HOST / MYSQL_USER / MYSQL_PASS / AWS 配置

# 3. 启动主看板
streamlit run app.py --server.port 8508

# 4. 构建/刷新缓存（服务器上每 30 分钟由 cron 自动运行）
python refresh_dashboard_cache.py
```

---

## 目录结构

```
nj-inventory-allocation/
│
├── app.py                   主 Streamlit 看板（端口 8508）
├── app_test_sfc.py          SFC→FC 测试视图（端口 8512）
├── app_test_fc2sfc.py       FC→SFC 测试视图（端口 8513）
├── cache_db.py              SQLite 缓存层 — 所有对 sfc_cache.db 的读写
├── config.py                所有可调参数（P 字典）
├── refresh_dashboard_cache.py     定时任务 — 每 30 分钟重建 sfc_cache.db
├── requirements.txt         Python 依赖
│
├── logic/                   核心业务逻辑
│   ├── fc_to_sfc.py         FC→SFC：MIDDLE 超储清出、TAIL 全量清仓、LOC_CLEAR 选库位
│   ├── sfc_to_fc.py         SFC→FC：T1/T2/T3/T4 任务列表构建
│   ├── putaway.py           上架决策引擎（TBD/TOP/MIDDLE/TAIL）
│   └── classify.py          SKU 分段分类辅助函数
│
├── data/
│   └── loader.py            所有 MySQL + Athena 查询（库存、批次、销量、到期日）
│
├── planning/
│   ├── ideal.py             理想库存状态计算
│   └── schedule.py          30 天滚动排程
│
├── tests/                   单元测试 + 基于属性的测试（pytest + Hypothesis）
│   ├── test_classify_*.py   分段分类测试
│   ├── test_fc_to_sfc_*.py  FC→SFC 逻辑测试
│   ├── test_sfc_to_fc_*.py  SFC→FC 逻辑测试
│   ├── test_putaway_*.py    上架决策测试
│   └── test_loader.py       数据加载测试
│
├── docs/                    文档
│   ├── LOGIC.md             ← 从这里开始：分段逻辑、数据源、转移规则
│   ├── design.md            详细设计规范（中文）：术语表、数据库字段、属性测试
│   └── ui-layout-guide.md   看板 UI 布局参考
│
├── scripts/                 一次性调试/部署/诊断脚本（非生产代码）
│   ├── deploy_*.py          服务器部署脚本
│   ├── check_*.py           数据验证脚本
│   ├── debug_*.py           调试脚本
│   └── *.sh                 Shell 工具脚本
│
└── results/                 运行时输出（.gitignore 已忽略，保留 .gitkeep）
    ├── sfc_cache.db         SQLite 缓存 — 由 refresh_dashboard_cache.py 重建
    └── athena_cache.csv     Athena 销量数据缓存（12 小时 TTL）
```

---

## 核心概念

### SKU 分段分类

**数据窗口：** 所有分类指标基于同一滚动窗口 — **该 SKU 最近 30 个有库存的日历天**（`available_qty > 0`，缺货日不计入，不要求连续）。

| 分段 | 判定条件 | 转移行为 |
|------|----------|----------|
| **TBD** | `inv_days_30 < 30` 或无 Athena 数据 | 不转移 — 在库历史不足 30 天（新品），等待分类 |
| **TOP** | `active_order_days ≥ 21` 且 `order_lines_30d ≥ 30` | SFC 全量 → FC |
| **TAIL** | `order_lines_30d < 8` | FC 全量 → SFC（清仓） |
| **MIDDLE** | 其余 | 按 D2S 双向转移 |

**字段定义：**
- `inv_days_30` — 最近 30 个有库存日期的天数（上限 30；< 30 = 新品 → TBD）
- `active_order_days` — 上述 30 个有库存日期中，实际有订单的天数（≤ `inv_days_30`）
- `order_lines_30d` — 上述 30 个有库存日期中的总订单行数
- `avg_daily_orders` — `order_lines_30d / 30`

### FC 库存天数（D2S）
```
fc_d2s = fc_qty / avg_daily_orders
```

### 转移触发条件

| 方向 | 触发 | 规则 |
|------|------|------|
| FC → SFC | MIDDLE: `fc_d2s > 30d` | 转出最新批次到 SFC，保留最老批次在 FC（FIFO） |
| FC → SFC | TAIL: `fc_qty > 0` | 全量清仓 |
| SFC → FC | TOP: `sfc_qty > 0` | SFC 全量 → FC |
| SFC → FC | MIDDLE: `fc_d2s < 14d` | 补货至目标 28d |
| SFC → FC | MIDDLE: `fc_d2s < 3d` | 紧急 T1 补货 |

### 缓存刷新流程

`refresh_dashboard_cache.py` 每 30 分钟运行一次：

```
[1] 库存数据（MySQL，实时）
[2] 销量数据（Athena，12 小时 TTL）
[3] 保质期标记（MySQL，12 小时 TTL）
[4] SKU 分段分类 → SQLite
[5] SFC 批次数据（MySQL，实时）
[6] FC 到期日汇总（MySQL，1 小时 TTL）
[7] 构建 T1-T4 任务列表 → SQLite
[8] FC→SFC 任务（MySQL，1 小时 TTL）
[9] 待上架数据（MySQL，30 分钟 TTL）
```

---

## 配置参数（`config.py`）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `mid_evict_d2s` | 30 | MIDDLE FC→SFC 触发线（fc_d2s 超过此值触发） |
| `mid_evict_buffer_d2s` | 3 | 转移后 FC 可降至 keep_d2s−3 |
| `mid_target_d2s` | 28 | SFC→FC 补货目标 D2S |
| `mid_emergency_d2s` | 3 | T1 紧急触发线 |
| `mid_replen_d2s` | 14 | T2 常规触发线 |
| `putaway_d2s` | 30 | 上架 MIDDLE 判断 FC 是否充足的阈值 |
| `top_trigger_d2s` | 28 | TOP SFC→FC 触发线 |

---

## 文档说明

| 文件 | 内容 |
|------|------|
| `docs/LOGIC.md` | **运营参考** — 分段逻辑、所有数据源、转移规则、上架决策树、文件地图。从这里开始看。 |
| `docs/design.md` | **设计规范（中文）** — 完整术语表、数据库字段验证、正确性属性、测试策略 |
| `docs/ui-layout-guide.md` | 看板 UI 布局指南 |

---

## 分支说明

| 分支 | 内容 |
|------|------|
| `master` | 生产版本（v4 DC 分配系统） |
| `archive/legacy-v1-v3` | 旧版存档：v1 BU 分段 · v2 销售层级 · v3 FC 分段 · Zone 2 SFC 订单 |
| `feature/v4-dc-allocation` | v4 开发历史 |
