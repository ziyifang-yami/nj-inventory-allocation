"""Fix indentation: from line 605 onwards, replace 8-space indent with 4-space."""
path = r"c:\Project-Z\NJ_Inventory_Allocation\v3_FC_segment\app.py"

with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find the line with "st.divider()" after the caption (around line 604)
# We fix from the first 8-space-indented line after "Dynamic ideal state"
start_fix = None
for i, line in enumerate(lines):
    if "Dynamic ideal state (no daily cap)" in line:
        start_fix = i + 1
        break

if start_fix is None:
    print("Marker not found"); exit(1)

print(f"Fixing from line {start_fix+1}")
fixed = 0
for i in range(start_fix, len(lines)):
    line = lines[i]
    if line.startswith("        "):  # 8 spaces
        lines[i] = "    " + line[8:]  # replace first 8 with 4
        fixed += 1
    # lines with less indentation (top-level or 4-space) left as-is

with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)

print(f"Fixed {fixed} lines")
