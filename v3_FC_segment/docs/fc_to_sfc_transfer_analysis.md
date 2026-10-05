# FC → SFC 转出方案分析

> 数据来源：Recom Details（Google Sheet）+ 流程图（Transfer from FC to SFC）+ planning_logic.md  
> 更新日期：2026-09-24

---

## 概览

FC 转出根据 SKU Segment 分三段独立处理：

| Segment | 转出策略 | 核心判断维度 |
|---------|---------|------------|
| **TOP** | 不转出，Keep in FC | AOD ≥ 21，OL ≥ 30 |
| **MIDDLE** | 条件转出（Excess only） | D2S / Location 数 / EXP 日期 |
| **BOTTOM** | 全量转出 | 进 Transfer Pool 排序执行 |

---

## 1. TOP — FC 转出

**结论：不参与任何 FC → SFC 转出。**

- TOP SKU 优先保留在 FC，保障 pick 效率
- 无触发 FC→SFC 的条件
- 若 SFC 有 TOP SKU 库存，反向触发 SFC→FC 转入（详见转入文档）

---

## 2. MIDDLE — FC 转出

### 2.1 触发前提

只有存在 **Excess**（FC 库存超出目标保留量）时才触发转出。

- FC 目标保留量：**30 D2S**
- 转出量 = FC 库存 − 30 D2S（即 Excess 部分）

### 2.2 四种情形（按 EXP × Location 数分类）

进入 MIDDLE 转出队列后，按以下 2 × 2 矩阵判断执行方式：

#### 情形 ① 无日期 + 多仓位（无 EXP，loc ≥ 2）

**目标：** 尽量整仓位转出，减少拆分。

执行步骤：
1. 找满足条件的 Location：`min_target_transfer - loc_qty ≥ 0`
2. 设 `target_qty = remaining`，迭代直到 Location 满足需求
3. 更新 `target_qty = remaining_qty`，继续迭代下一个 Location
4. 将选中的 Location 加入转出列表

#### 情形 ② 无日期 + 单仓位（无 EXP，loc = 1）

**目标：** FC 保留 30 D2S，仅转出 Excess 部分。

- 转出量 = `fc_qty − 30 D2S`
- 从唯一的 Location 中拆出 Excess 数量转出

#### 情形 ③ 有日期 + 多仓位（有 EXP，loc ≥ 2）

**目标：** 好日期的库存优先转 SFC（让 SFC 持有更好的日期），FC 保留日期较近（较差）的库存。

执行步骤：
1. 按 EXP 日期由远到近（好日期 → 差日期）排序 Location
2. 从日期最好的 Location 开始，`remaining = target - loc_qty ≥ 0`
3. 迭代直到 target 满足
4. 选中 Location 加入转出列表

**附加判断：**  
若 SFC 已有该 SKU 库存且 SFC 日期比 FC 好 → 触发 **SFC Bin→Stock（Date Switch Task）**，而非直接转出新库存。

#### 情形 ④ 有日期 + 单仓位（有 EXP，loc = 1）

**目标：** FC 保留 30 D2S，仅转出 Excess。

- 与情形 ② 相同逻辑，但转出时按 EXP 日期由远到近选批次

---

### 2.3 MIDDLE 转出流程图（算法视角）

```
MIDDLE SKU 排序入口
  Sort by: Excess Volume DESC → Qty DESC
           ↓
     Has EXP 日期?
     ├── NO → Single Loc?
     │        ├── YES → 情形 ②：转出 Excess（fc_qty - 30 D2S）
     │        └── NO  → 情形 ①：迭代选 Location，整仓位优先
     │
     └── YES → Single Loc?
              ├── YES → 情形 ④：EXP 排序后转出 Excess
              └── NO  → 情形 ③：EXP 由远到近选 Location
                        触发阈值：Excess > 3 D2S（10% qty buffer）
```

> **注：** EXP 多仓位情形有额外的 10% qty buffer（Excess > 3 D2S 才触发），防止频繁小量转移。

---

## 3. BOTTOM — FC 转出

### 3.1 触发条件

**全量转出。** 只要 FC 有库存（`fc_qty > 0`），全部进入 Transfer Pool。

### 3.2 Transfer Pool 排序规则

所有满足转出条件的 BOTTOM 库存统一进池，按以下优先级排序：

**优先级 1 — 整仓位优先**  
若同一 Location 内所有 SKU 均需转出 → 优先合并整仓位转出，减少拆分操作。

**优先级 2 — EXP 提权（BOTTOM 专属）**  
若 SFC 现有库存日期比 FC 好 → 提高该 BOTTOM SKU 的转出优先级（让 FC 的差日期库存尽快清出）。

**优先级 3 — Volume 排序**  
完成 Location Selection 后，按 **Total Transfer Volume 倒序** 排列最终转出顺序（体积大的先转，提升运力效率）。

---

## 4. Transfer Plan Generator（作业计划生成）

以上 MIDDLE + BOTTOM 进入 Transfer Pool 后，统一经过 Transfer Plan Generator 生成每日作业计划：

### 步骤

**Step 1 — 排序**  
按 Tier 排序（Bottom → Middle），Middle 内按 Excess Volume 排序。

**Step 2 — 设定每日目标**  
设置每日转移目标数量（如 100 SKU），截取 Transfer List 前 N 个 SKU。

**Step 3 — Location Type 分配**  
基于 SFC/FC 回传 SKU 信息，为每个待转 SKU 分配合适的目标 location type 及所需数量。

**Step 4 — Location 释放校验（关键回路）**  
检查：FC/SFC 释放的 locations + 可操作 locations（manipulatable）≤ putaway 所需最小 locations

- **满足** → 保持列表顺序
- **不满足** → 将第一个 MIDDLE SKU 移到列表顶部，迭代直到满足 location 释放需求

> 此步骤防止"转出去但 FC 没有足够空间接收新 putaway"的情况。

**Step 5 — 按 Location 重排输出**  
最终 Transfer List 按 location_no 重新排序，方便司机按区域顺序完成任务，减少行走路程。

---

## 5. 方案对比表

| 维度 | Recom Details（业务规则层）| 流程图（算法执行层）|
|------|--------------------------|------------------|
| TOP 转出 | 明确不参与 | Keep in FC，流程未涉及 |
| MIDDLE 触发阈值 | Excess > 30 D2S | Excess > 3 D2S（10% buffer，EXP 多仓位情形）|
| MIDDLE EXP 处理 | 好日期转 SFC，坏日期留 FC | EXP 由远到近排序选 Location |
| MIDDLE 单 vs 多仓位 | 明确 4 种情形 | Single Loc 判断分支体现 |
| BOTTOM 处理 | 全量进池，3 条排序规则 | All Transfer Out，进 Transfer Plan Generator |
| 整仓位优先 | 明确：同 Location 所有 SKU 均需转出则合并 | 隐含在 Transfer Plan Generator Step 1 |
| EXP 提权 | BOTTOM 专属，SFC 日期好 → 提高优先级 | 通用 Has EXP 分支处理 |
| Volume 排序 | Total Transfer Volume 倒序 | Excess Volume DESC + Qty DESC |
| 每日任务量控制 | 提到按每周任务量排序 | 明确每日 target 数量（如 100 SKU）|
| Location 释放校验 | 未明确 | Step 4 独有：释放 + manipulatable ≤ putaway 需求 |
| 输出顺序 | 按 location 顺序减少行走 | 按 location 重排给司机 |

---

## 6. 待对齐问题

| # | 问题 | 涉及方 |
|---|------|--------|
| 1 | MIDDLE Excess 阈值：是 30 D2S 保留（Sheet）还是带 3 D2S buffer（流程图）？需统一 | planning_logic.md 需更新 |
| 2 | Location 释放校验逻辑（流程图 Step 4）未在 Sheet 业务规则中体现，实现时易遗漏 | Recom Details 需补充 |
| 3 | Bottom EXP 提权的具体数值标准（SFC 日期好多少天才触发？）Sheet 无定义 | Recom Details 需补充 |
| 4 | MIDDLE 情形 ③ 的 10% qty buffer（Excess > 3 D2S）是否适用于所有 EXP 多仓位情形，还是仅流程图特定逻辑 | 需确认 |
