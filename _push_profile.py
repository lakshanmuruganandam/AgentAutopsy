"""Push the profile README to Abhisekhpatel/Abhisekhpatel via GitHub API."""
import base64
import json
import subprocess
import sys
from pathlib import Path

README_PATH = Path(__file__).parent / "_profile_readme.md"
REPO = "Abhisekhpatel/Abhisekhpatel"
FILE_PATH = "README.md"
COMMIT_MSG = "professional profile README"
BRANCH = "main"

# ── 1. Get current file SHA ────────────────────────────────────────────────
sha_result = subprocess.run(
    ["gh", "api", f"repos/{REPO}/contents/{FILE_PATH}", "--jq", ".sha"],
    capture_output=True, text=True,
)
sha = sha_result.stdout.strip()
if not sha:
    print(f"ERROR: could not get SHA — {sha_result.stderr.strip()}", file=sys.stderr)
    sys.exit(1)
print(f"current SHA: {sha}")

# ── 2. Read new content and base64-encode it ──────────────────────────────
content_raw = README_PATH.read_text(encoding="utf-8")
content_b64 = base64.b64encode(content_raw.encode("utf-8")).decode("ascii")

# ── 3. Push via GitHub API ─────────────────────────────────────────────────
payload = json.dumps({
    "message": COMMIT_MSG,
    "content": content_b64,
    "sha": sha,
    "branch": BRANCH,
})
push_result = subprocess.run(
    ["gh", "api", f"repos/{REPO}/contents/{FILE_PATH}",
     "--method", "PUT",
     "--input", "-"],
    input=payload.encode("utf-8"),
    capture_output=True,
)
if push_result.returncode != 0:
    print(f"PUSH FAILED:\n{push_result.stderr.decode()}", file=sys.stderr)
    sys.exit(1)

response = json.loads(push_result.stdout.decode("utf-8"))
commit_sha = response.get("commit", {}).get("sha", "(unknown)")
print(f"pushed OK — commit {commit_sha}")
print(f"https://github.com/{REPO}")
