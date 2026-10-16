import subprocess, time
SSH = ['ssh','-o','ConnectTimeout=10','-o','BatchMode=yes','root@184.33.17.231']
def run(cmd):
    r = subprocess.run(SSH+[cmd], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()

print("=== CSS in app.py ===")
print(run("grep -n 'max-width\\|32px\\|stTextInput' /opt/inv-allocation/app.py | head -10"))

print("\n=== CSS in app_test_sfc.py ===")
print(run("grep -n 'max-width\\|32px\\|stTextInput' /opt/inv-allocation/app_test_sfc.py | head -10"))

print("\n=== columns ratio in _render_sfc (app.py) ===")
print(run("grep -n 'st.columns.*1\\.' /opt/inv-allocation/app.py | head -10"))

print("\n=== columns ratio in _render_table (app_test_sfc.py) ===")
print(run("grep -n 'st.columns.*1\\.' /opt/inv-allocation/app_test_sfc.py | head -10"))

time.sleep(60)
