# V1 — Segment-based Allocation

## 方案说明

用 `nj_segment`（来自 `dwb_trade.trade_item_sale_agg_daily`）作为分层依据。

## 已知问题

- `nj_segment` 包含 `New`、`Trendy` 等 PPT 未定义的值，归入 DC_DIRECT 处理
- `TBD` segment 占比过高（37,961 SKU），全部触发迁出，结果不可靠
- segment 更新频率、覆盖范围可能导致分层不稳定

## 运行结果（2026-09-10）

| Action | SKU | Units |
|--------|-----|-------|
| KEEP | 9,124 | — |
| TRANSFER_TO_SFC | 43,563 | ~4.46M |
| RETURN_FROM_SFC | 5,085 | ~193K |

## Segment → 模式 映射

| Segment | 模式 | D2S 区间 | 补回触发 |
|---------|------|---------|---------|
| Star/Hit/Head | FC_PRIMARY | 35–45 | <35 |
| Middle | DUAL_WH | 28–35 | <10 |
| Longtail/TBD/New/Trendy | DC_DIRECT | 14–28 | <7 |
