import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { getDbPath, getWorkspaceRoot, runPythonJson } from "./pythonBridge";

export interface FailedRunDiagnostic {
  runId: string;
  agentName: string;
  message: string;
  filePath?: string;
  line: number;
}

const QUERY_FAILURES = `
import json
import sqlite3
from pathlib import Path

db_path = Path("agentautopsy.db")
result = []
if not db_path.exists():
    print(json.dumps(result))
    raise SystemExit(0)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

def parse_payload(raw):
    if not raw:
        return {}
    try:
        import json as _json
        data = _json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

runs = conn.execute(
    "SELECT id, agent_name, status FROM runs ORDER BY start_time DESC"
).fetchall()

for run in runs:
    run_id = run["id"]
    error_row = conn.execute(
        'SELECT payload FROM events WHERE run_id = ? AND type = ? ORDER BY timestamp LIMIT 1',
        (run_id, "error"),
    ).fetchone()
    if not error_row:
        continue
    payload = parse_payload(error_row["payload"])
    root_cause = f"{payload.get('error_type', 'Error')}: {payload.get('message', '')}"
    line = 0
    file_path = None
    for key in ("file", "filename", "path"):
        if payload.get(key):
            file_path = str(payload[key])
            break
    for key in ("line", "lineno", "line_number"):
        if payload.get(key) is not None:
            try:
                line = max(0, int(payload[key]) - 1)
            except (TypeError, ValueError):
                line = 0
            break
    stack = payload.get("stack") or payload.get("traceback") or ""
    if stack and file_path is None:
        for stack_line in str(stack).splitlines():
            if ":" in stack_line and ".py" in stack_line:
                parts = stack_line.strip().split(":")
                if len(parts) >= 2:
                    file_path = parts[0].strip()
                    try:
                        line = max(0, int(parts[1]) - 1)
                    except ValueError:
                        pass
                    break
    patch = None
    try:
        patch = conn.execute(
            "SELECT patch FROM fix_cache WHERE failure_type = ? ORDER BY hits DESC LIMIT 1",
            (payload.get("error_type", ""),),
        ).fetchone()
    except sqlite3.OperationalError:
        patch = None
    if patch and patch["patch"]:
        for part in str(patch["patch"]).splitlines():
            if part.startswith("ROOT CAUSE:"):
                root_cause = part[len("ROOT CAUSE:"):].strip()
                break
    result.append({
        "runId": run_id,
        "agentName": run["agent_name"] or "agent",
        "message": root_cause,
        "filePath": file_path,
        "line": line,
    })

conn.close()
print(json.dumps(result))
`;

export async function fetchFailedRuns(
  root: string
): Promise<FailedRunDiagnostic[]> {
  if (!fs.existsSync(getDbPath(root))) {
    return [];
  }
  return runPythonJson<FailedRunDiagnostic[]>(root, QUERY_FAILURES);
}

export async function updateFailureDiagnostics(
  collection: vscode.DiagnosticCollection
): Promise<FailedRunDiagnostic[]> {
  const root = getWorkspaceRoot();
  if (!root) {
    collection.clear();
    return [];
  }

  let failures: FailedRunDiagnostic[] = [];
  try {
    failures = await fetchFailedRuns(root);
  } catch {
    collection.clear();
    return [];
  }

  collection.clear();
  const openPythonDocs = vscode.workspace.textDocuments.filter(
    (doc) => doc.languageId === "python" && !doc.isUntitled
  );

  for (const failure of failures) {
    let targetUri: vscode.Uri | undefined;

    if (failure.filePath) {
      const absolute = path.isAbsolute(failure.filePath)
        ? failure.filePath
        : path.join(root, failure.filePath);
      targetUri = vscode.Uri.file(absolute);
    } else if (openPythonDocs.length > 0) {
      targetUri = openPythonDocs[0].uri;
    } else {
      const matches = await vscode.workspace.findFiles("**/*.py", "**/node_modules/**", 1);
      if (matches[0]) {
        targetUri = matches[0];
      }
    }

    if (!targetUri) {
      continue;
    }

    const doc = await vscode.workspace.openTextDocument(targetUri);
    const line = Math.min(
      Math.max(failure.line, 0),
      Math.max(doc.lineCount - 1, 0)
    );
    const range = new vscode.Range(line, 0, line, 120);
    const diagnostic = new vscode.Diagnostic(
      range,
      `[AgentAutopsy · ${failure.agentName}] ${failure.message}`,
      vscode.DiagnosticSeverity.Error
    );
    diagnostic.source = "AgentAutopsy";
    diagnostic.code = failure.runId;
    collection.set(targetUri, [diagnostic]);
  }

  return failures;
}