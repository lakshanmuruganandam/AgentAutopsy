import * as vscode from "vscode";
import { getWorkspaceRoot, runPythonJson } from "./pythonBridge";

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

let activePanel: vscode.WebviewPanel | undefined;

function getWebviewHtml(baseHtml: string): string {
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

export async function openAgentAutopsyPanel(
  context: vscode.ExtensionContext
): Promise<void> {
  const root = getWorkspaceRoot();
  if (!root) {
    vscode.window.showErrorMessage("Open a workspace folder to use AgentAutopsy.");
    return;
  }

  let html = "<html><body><h1>Loading AgentAutopsy...</h1></body></html>";
  try {
    const payload = await runPythonJson<{ html: string }>(root, GENERATE_HTML, 120000);
    html = getWebviewHtml(payload.html);
  } catch (error) {
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

  activePanel = vscode.window.createWebviewPanel(
    "agentautopsy",
    "AgentAutopsy",
    vscode.ViewColumn.Beside,
    {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [vscode.Uri.file(root)],
    }
  );

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
