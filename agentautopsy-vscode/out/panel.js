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
exports.openAgentAutopsyPanel = openAgentAutopsyPanel;
const vscode = __importStar(require("vscode"));
const pythonBridge_1 = require("./pythonBridge");
const GENERATE_HTML = `
import json
from agentautopsy.db import get_db, create_tables
from agentautopsy.ui import _load_data, _build_html, build_agent_chains

db = get_db()
create_tables(db)
runs, runs_data = _load_data(db)
chains = build_agent_chains(runs, runs_data)
html = _build_html(runs, runs_data, chains)
print(json.dumps({"html": html}))
`;
let activePanel;
function getWebviewHtml(baseHtml) {
    const bridgeScript = `
<script>
(function() {
  const vscodeApi = acquireVsCodeApi();
  document.addEventListener("click", function(event) {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const btn = target.closest(".autofix-btn");
    if (!btn || !btn.id || !btn.id.startsWith("autofix-btn-")) return;
    const runId = btn.id.replace("autofix-btn-", "");
    event.stopPropagation();
    event.preventDefault();
    vscodeApi.postMessage({ type: "autofix", runId: runId });
  }, true);
  window.addEventListener("message", function(event) {
    const msg = event.data;
    if (!msg || msg.type !== "autofixResult") return;
    const status = document.getElementById("autofix-status");
    if (!status) return;
    status.textContent = msg.message || "";
    status.classList.remove("success", "error");
    if (msg.success) status.classList.add("success");
    else status.classList.add("error");
  });
})();
</script>
`;
    if (baseHtml.includes("</body>")) {
        return baseHtml.replace("</body>", `${bridgeScript}</body>`);
    }
    return baseHtml + bridgeScript;
}
async function openAgentAutopsyPanel(context) {
    const root = (0, pythonBridge_1.getWorkspaceRoot)();
    if (!root) {
        vscode.window.showErrorMessage("Open a workspace folder to use AgentAutopsy.");
        return;
    }
    let html = "<html><body><h1>Loading AgentAutopsy...</h1></body></html>";
    try {
        const payload = await (0, pythonBridge_1.runPythonJson)(root, GENERATE_HTML, 120000);
        html = getWebviewHtml(payload.html);
    }
    catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        vscode.window.showErrorMessage(`Failed to load AgentAutopsy UI: ${message}`);
        html = `<html><body style="font-family:sans-serif;padding:2rem;color:#f87171;">
      <h2>AgentAutopsy UI failed to load</h2>
      <pre>${message}</pre>
      <p>Ensure <code>pip install agentautopsy</code> and run from workspace root.</p>
    </body></html>`;
    }
    if (activePanel) {
        activePanel.webview.html = html;
        activePanel.reveal(vscode.ViewColumn.Beside);
        return;
    }
    activePanel = vscode.window.createWebviewPanel("agentautopsy", "AgentAutopsy", vscode.ViewColumn.Beside, {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.file(root)],
    });
    activePanel.webview.html = html;
    activePanel.webview.onDidReceiveMessage(async (message) => {
        if (!message || message.type !== "autofix" || !message.runId) {
            return;
        }
        const terminal = vscode.window.createTerminal("AgentAutopsy Fix");
        terminal.show();
        terminal.sendText(`agentautopsy fix ${message.runId}`);
        activePanel?.webview.postMessage({
            type: "autofixResult",
            success: true,
            message: `Running: agentautopsy fix ${message.runId} (see terminal)`,
        });
    });
    activePanel.onDidDispose(() => {
        activePanel = undefined;
    });
}
//# sourceMappingURL=panel.js.map