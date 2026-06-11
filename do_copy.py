import os
import shutil
import subprocess
import sys

BASE = r"C:\Users\abhishek\.cursor\projects\empty-window\agentautopsy"
SRC = r"C:\Users\abhishek\Downloads\autoagentpsy.abhisekh.png"
DST = os.path.join(BASE, "assets", "logo.png")
RESULT = os.path.join(BASE, "copy-result.txt")

lines = []

# 1. Create assets directory
assets_dir = os.path.join(BASE, "assets")
os.makedirs(assets_dir, exist_ok=True)
lines.append(f"assets dir: {assets_dir} (exists={os.path.isdir(assets_dir)})")

# 2. Copy file
if os.path.isfile(SRC):
    shutil.copy2(SRC, DST)
    lines.append(f"copied {SRC} -> {DST}")
    # 3. Delete source after successful copy
    if os.path.isfile(DST):
        os.remove(SRC)
        lines.append(f"deleted source: {SRC}")
    else:
        lines.append("ERROR: copy failed, dest missing")
elif os.path.isfile(DST):
    lines.append(f"source already gone, dest exists: {DST}")
else:
    lines.append("ERROR: neither source nor dest found")

# Check logo.png
if os.path.isfile(DST):
    size = os.path.getsize(DST)
    lines.append(f"assets/logo.png EXISTS size={size} bytes")
else:
    lines.append("assets/logo.png MISSING")

# 4. Git operations
os.chdir(BASE)
for cmd in [
    ["git", "add", "."],
    ["git", "commit", "-m", "professional README with logo v2.0.0"],
    ["git", "push", "origin", "master"],
]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        lines.append(f"CMD: {' '.join(cmd)}")
        lines.append(f"  exit={r.returncode}")
        if r.stdout.strip():
            lines.append(f"  stdout: {r.stdout.strip()}")
        if r.stderr.strip():
            lines.append(f"  stderr: {r.stderr.strip()}")
    except Exception as e:
        lines.append(f"CMD FAILED: {' '.join(cmd)} -> {e}")

with open(RESULT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("\n".join(lines))
