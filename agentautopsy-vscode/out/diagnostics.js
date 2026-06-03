"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.fetchFailedRuns = fetchFailedRuns;
exports.updateFailureDiagnostics = updateFailureDiagnostics;
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
const vscode = __importStar(require("vscode"));
const pythonBridge_1 = require("./pythonBridge");
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
async function fetchFailedRuns(root) {
    if (!fs.existsSync((0, pythonBridge_1.getDbPath)(root))) {
        return [];
    }
    return (0, pythonBridge_1.runPythonJson)(root, QUERY_FAILURES);
}
async function updateFailureDiagnostics(collection) {
    const root = (0, pythonBridge_1.getWorkspaceRoot)();
    if (!root) {
        collection.clear();
        return [];
    }
    let failures = [];
    try {
        failures = await fetchFailedRuns(root);
    }
    catch {
        collection.clear();
        return [];
    }
    collection.clear();
    const openPythonDocs = vscode.workspace.textDocuments.filter((doc) => doc.languageId === "python" && !doc.isUntitled);
    for (const failure of failures) {
        let targetUri;
        if (failure.filePath) {
            const absolute = path.isAbsolute(failure.filePath)
                ? failure.filePath
                : path.join(root, failure.filePath);
            targetUri = vscode.Uri.file(absolute);
        }
        else if (openPythonDocs.length > 0) {
            targetUri = openPythonDocs[0].uri;
        }
        else {
            const matches = await vscode.workspace.findFiles("**/*.py", "**/node_modules/**", 1);
            if (matches[0]) {
                targetUri = matches[0];
            }
        }
        if (!targetUri) {
            continue;
        }
        const doc = await vscode.workspace.openTextDocument(targetUri);
        const line = Math.min(Math.max(failure.line, 0), Math.max(doc.lineCount - 1, 0));
        const range = new vscode.Range(line, 0, line, 120);
        const diagnostic = new vscode.Diagnostic(range, `[AgentAutopsy · ${failure.agentName}] ${failure.message}`, vscode.DiagnosticSeverity.Error);
        diagnostic.source = "AgentAutopsy";
        diagnostic.code = failure.runId;
        collection.set(targetUri, [diagnostic]);
    }
    return failures;
}
//# sourceMappingURL=diagnostics.js.map