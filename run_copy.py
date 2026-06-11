import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(r"C:\Users\abhishek\.cursor\projects\empty-window\agentautopsy")
SRC = Path(r"C:\Users\abhishek\Downloads\autoagentpsy.abhisekh.png")
DST = ROOT / "assets" / "logo.png"
RESULT = ROOT / "copy-result.txt"

lines = []

def log(msg):
    lines.append(msg)
    print(msg)

try:
    assets_dir = ROOT / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    log(f"assets directory: {'created' if not DST.parent.exists() else 'exists'} -> {assets_dir}")

    if not SRC.exists():
        raise FileNotFoundError(f"Source not found: {SRC}")

    shutil.copy2(SRC, DST)
    log(f"Copied {SRC} -> {DST}")

    if not DST.exists():
        raise RuntimeError("Copy reported success but destination missing")

    dst_size = DST.stat().st_size
    log(f"assets/logo.png exists: True, size: {dst_size} bytes")

    SRC.unlink()
    log(f"Deleted source: {SRC} (exists after delete: {SRC.exists()})")

    os.chdir(ROOT)

    def run_git(args):
        r = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        out = (r.stdout or "") + (r.stderr or "")
        log(f"git {' '.join(args)} -> exit {r.returncode}")
        if out.strip():
            log(out.strip())
        return r

    run_git(["add", "."])
    commit = run_git(["commit", "-m", "professional README with logo v2.0.0"])
    push = run_git(["push", "origin", "master"])

    log(f"git push result: exit_code={push.returncode}")
    if push.returncode == 0:
        log("git push: SUCCESS")
    else:
        log("git push: FAILED")

except Exception as e:
    log(f"ERROR: {type(e).__name__}: {e}")

RESULT.write_text("\n".join(lines) + "\n", encoding="utf-8")
