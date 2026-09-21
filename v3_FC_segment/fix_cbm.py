"""Replace all cbm/CBM labels with cbi/CBI in app.py (display labels only, not column reads from CSV)."""
path = r"c:\Project-Z\NJ_Inventory_Allocation\v3_FC_segment\app.py"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

replacements = [
    # display labels & variable names in app code
    ("unit_volume_cbm",     "unit_volume_cbi"),
    ("daily_sales_cbm",     "daily_sales_cbi"),
    ("fc_volume_cbm",       "fc_volume_cbi"),
    ("sfc_volume_cbm",      "sfc_volume_cbi"),
    ("volume_cbm",          "volume_cbi"),
    ("FC Vol(CBM)",         "FC Vol(in³)"),
    ("SFC Vol(CBM)",        "SFC Vol(in³)"),
    ("Total Volume (CBM)",  "Total Volume (in³)"),
    ('"CBM"',               '"in³"'),
    ("CBM %",               "CBI %"),
    ("Daily CBM",           "Daily in³"),
    ("# volume (CBM)",      "# volume (in³)"),
    ("t_cbm",               "t_cbi"),
]

for old, new in replacements:
    count = content.count(old)
    content = content.replace(old, new)
    if count:
        print(f"  {old!r:40} → {new!r}  ({count}x)")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("\nDone.")
