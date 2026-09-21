# V2 — Sales Tier Based Allocation

## 与 V1 的区别

| | V1 | V2 |
|--|---|---|
| 分层来源 | `nj_segment` (文字) | `adf_product_segment.segment` (数字 1-8) |
| TBD 问题 | ❌ 37,961 SKU 是 TBD，全部触发迁出 | ✅ 全部 SKU 有明确数字 tier |
| Segment 定义 | 模糊，含 New/Trendy 无规则 | ✅ wiki 已确认含义 |

## Segment 映射（wiki 确认，2026-09-10）

| 数字 | 名称 | 模式 | D2S 区间 |
|------|------|------|---------|
| 1 | Hit / 爆品 | FC_PRIMARY | 35–45 |
| 2 | Head / 头部 | FC_PRIMARY | 35–45 |
| 6 | Star | FC_PRIMARY | 35–45 |
| 3 | Middle | DUAL_WH | 28–35 |
| 7 | New | DUAL_WH | 28–35 |
| 8 | Trendy | DUAL_WH | 28–35 |
| 4 | Longtail | DC_DIRECT | 14–28 |
| 5 | Pending/TBD | DC_DIRECT | 14–28 |

## 运行方法

```bash
python run_allocation.py
```

## 待讨论

- [ ] Pending (segment=5) 的处理是否过于激进？43K SKU 全归 DC_DIRECT
- [ ] D2S 用 30 天窗口还是对齐 wiki 用 ads90（90天有库存天）？
