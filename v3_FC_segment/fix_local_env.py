content = open(r'c:\Project-Z\NJ_Inventory_Allocation\v3_FC_segment\app.py', encoding='utf-8').read()

old = '''    from dotenv import load_dotenv
    from pathlib import Path
    try:
        load_dotenv(dotenv_path=Path(".") / ".env")'''

new = '''    from pathlib import Path
    try:
        _load_env()'''

count = content.count(old)
content2 = content.replace(old, new)
open(r'c:\Project-Z\NJ_Inventory_Allocation\v3_FC_segment\app.py', 'w', encoding='utf-8').write(content2)
print(f"Replaced {count} occurrences")
