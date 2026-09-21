content = open('/opt/inv-allocation/app.py').read()
old = 'Path("..") / ".env"'
new = 'Path(".") / ".env"'
count = content.count(old)
content2 = content.replace(old, new)
open('/opt/inv-allocation/app.py', 'w').write(content2)
print(f"Replaced {count} occurrences of dotenv path")
