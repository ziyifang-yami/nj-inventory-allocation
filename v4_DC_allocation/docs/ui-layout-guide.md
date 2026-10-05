# UI Layout Guide — app_test_sfc.py

## Deploy & Test Workflow

After editing any parameter in `app_test_sfc.py`:

**1. Upload to server (one command):**
```
scp "c:\Project-Z\NJ_Inventory_Allocation\v4_DC_allocation\app_test_sfc.py" root@184.33.17.231:/opt/inv-allocation/app_test_sfc.py
```

**2. Refresh browser** — Streamlit auto-reloads, no restart needed.
- URL: http://zbot-agent.yamibuy.net:8512/

**3. When satisfied, commit:**
```
cd c:\Project-Z\NJ_Inventory_Allocation
git add v4_DC_allocation/app_test_sfc.py
git commit -m "ui: describe your change"
```

---

## Pagination Nav — Parameter Map

The pagination footer is in `_render_table()` function, around line 497.

### 1. Nav position (how far right)
```python
_nav_left, _nav_right = st.columns([5.0, 0.8])
```
| Param | Controls | Change |
|-------|----------|--------|
| `5.0` | Spacer on left | Bigger → nav pushed further right |
| `0.8` | Width of entire nav block | Smaller → elements closer together |

**✅ PERFECT baseline: `[5.0, 0.8]`**

---

### 2. Inner column widths (spacing between elements)
```python
_c1, _c2, _c3, _c4, _c5 = st.columns([0.2, 0.2, 0.2, 0.2, 0.2])
```
| Column | Element | Increase = |
|--------|---------|-----------|
| `_c1 = 0.2` | `‹` prev button | more space around `‹` |
| `_c2 = 0.2` | `Page` text | more space, won't wrap |
| `_c3 = 0.2` | page number input | wider input field |
| `_c4 = 0.2` | `/ 43` total text | more space for total |
| `_c5 = 0.2` | `›` next button | more space around `›` |

**✅ PERFECT baseline: `[0.2, 0.2, 0.2, 0.2, 0.2]`**

> **Important:** `_c2` must be wide enough or "Page" wraps to two lines.  
> Always keep `white-space:nowrap` in the `Page` markdown style.

---

### 3. Page input box width (CSS)
```python
div[data-testid="stTextInput"] > div > div > input {
    max-width: 32px !important;
}
```
**✅ PERFECT baseline: `32px`**

---

### 4. Font size and vertical alignment of "Page" and "/ 43" text
```python
# _c2 — "Page" text
'font-size:1rem;padding-top:8px'

# _c4 — "/ 43" text  
'font-size:1rem;padding-top:8px'
```
**✅ PERFECT baseline: `font-size:1rem`, `padding-top:8px`**

- `font-size` controls text size: `0.82rem` = small, `1rem` = default body, `1.1rem` = slightly larger
- `padding-top` controls vertical position — adjust if text looks too high/low after font change

---

### 5. Font size of "Page" and "/ 43"
```python
'font-size:0.82rem'   # _c2 and _c4 markdown style
```
- `0.82rem` ≈ 13px
- `0.9rem` ≈ 14px  
- `1rem` = default body (~16px)

---

## Quick Fixes Reference

| Problem | Fix |
|---------|-----|
| Nav too far right | Decrease `5.2` in outer columns |
| Nav elements too spread | Decrease `1.6` outer nav width |
| `‹` / `›` too far from text | Decrease `_c1` / `_c5` |
| "Page" wraps to 2 lines | Increase `_c2` **or** ensure `white-space:nowrap` is in style |
| Input too wide | Decrease `max-width` in CSS |
| Text not vertically aligned | Adjust `padding-top` in `_c2` and `_c4` markdown |

---

## Filter Row Parameters

```python
_fc1, _fc2, _fc3, _fc4 = st.columns([1.2, 1, 1, 0.7])
```
| Column | Element | Notes |
|--------|---------|-------|
| `_fc1 = 1.2` | 🔍 SKU search input | Wider than others |
| `_fc2 = 1.0` | Segment dropdown | Equal width |
| `_fc3 = 1.0` | Loc Type dropdown | Equal width |
| `_fc4 = 0.7` | Rows/Page dropdown | Slightly narrower |
