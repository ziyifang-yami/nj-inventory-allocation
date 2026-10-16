# v4 Dev Notes & Diagnostic Findings

## Known Test Failure (Property-Based Test)

**File:** `tests/test_sfc_to_fc_properties.py::test_stock_case_rounding_invariant`  
**Status:** FAILED (1 of 3 PBT tests)

**Failing example:**
```
lots=[
  {location_no:'0', qty:1, loc_type:'Stock', case_qty:2, need_minimal_exp:0},
  {location_no:'0', qty:26, loc_type:'Stock', case_qty:1, need_minimal_exp:0}
]
adl=1.0, fc_qty=1.0
```
**Error:** `Stock loc case-rounding violated: 1.0 % 2 = 1.0`  
**Root cause:** When a lot qty (1) is smaller than case_qty (2), the whole lot is taken as-is. The invariant "transfer is always a multiple of case_qty" breaks for sub-case lots. This is intentional behavior (take whole lot) but the test assumption is too strict.  
**Resolution needed:** Relax the test assertion to allow `xfer <= case_qty OR xfer % case_qty == 0`.

---

## DB Schema Reference: `wh_inbound_batch`

Key columns (from diagnostic run):

| Column | Type | Notes |
|--------|------|-------|
| `qc_ExpiredDate` | int (unix) | Expiry date — 0 = no expiry |
| `in_dtm` | int (unix) | Inbound receipt time |
| `pieces_per_pack` | varchar(30) | Case qty — same as `wh_item_setting.spec` |
| `lot_id` | bigint | Links to `wh_lot_master.lot_id` |
| `putaway_qty` | int | Only valid for batch putaway |
| `product_dtm` | int | Production date — 0 = invalid |
| `shelf_life` | int | Shelf life in days — 0 = invalid |

**Source number format:**
- Self-operated PO: `0022609030039` (13 digits, leading 002)
- FBY shipment: `F2608081560065` (starts with F)

---

## FC Lot Query — Correct Join for EXP Date

Use `lot_id` join (not `inbound_number`):
```sql
JOIN yamibuy_wh.wh_inbound_batch ib ON ib.lot_id = li.lot_id
```
- FC lot rows with exp date via this join: ~46,154
- `pieces_per_pack` from `wh_inbound_batch` matches `im_item_extend` for most SKUs
- Exception: some SKUs have batch_ppp ≠ item_ppp (use `wh_item_setting.spec` as source of truth)

---

## case_qty Source of Truth

`wh_item_setting.spec` (varchar, warehouse-specific) is the correct source.  
`im_item_extend.pieces_per_pack` is a fallback but can differ for some SKUs.

---

## Zone Definition (confirmed 2026-10)

- **SFC** = `zone_id = 2`, `location_type IN (3,4)`, `warehouse_number = '002'`
- **FC**  = `zone_id != 2`, `location_type IN (3,4)`, `warehouse_number = '002'`
- Size exclusions: `size_id NOT IN (13, 33)`
- Business type exclusion: `IFNULL(ii.business_type, 0) != 8`

---

## PO Reference for RECG Putaway Items

```sql
-- From wh_inventory_transaction_log
WHERE reference_type IN ('PO System(Sellable)', 'Seller shipment System(Sellable)')
```
- Self-operated: `reference_type='PO System(Sellable)'` → `reference_id` = PO number
- FBY: `reference_type='Seller shipment System(Sellable)'` → `reference_id` = F-shipment number

**Performance note:** Full JOIN on `wh_inventory_transaction_log` is very slow (~337s).  
Use two-query approach: Q1 get RECG items (fast), Q2 look up PO refs for those ~100 items only.
