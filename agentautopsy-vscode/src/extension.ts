import * as vscode from "vscode";
import { updateFailureDiagnostics } from "./diagnostics";
import { openAgentAutopsyPanel } from "./panel";
import {
  getWorkspaceRoot,
  isAgentAutopsyInstalled,
} from "./pythonBridge";

let statusBarItem: vscode.StatusBarItem;
let diagnosticCollection: vscode.DiagnosticCollection;
const seenFailureRunIds = new Set<string>();
let watchingEnabled = false;

async function ensureInstalled(): Promise<boolean> {
  const installed = await isAgentAutopsyInstalled();
  if (installed) {
    return true;
  }

  const install = "Install AgentAutopsy";
  const selection = await vscode.window.showInformationMessage(
    "AgentAutopsy is not installed.",
    install
  );
  if (selection === install) {
    const terminal = vscode.window.createTerminal("AgentAutopsy Install");
    terminal.show();
    terminal.sendText("pip install agentautopsy");
  }
  return false;
}

function updateStatusBar(text: string, tooltip?: string): void {
  statusBarItem.text = text;
  if (tooltip) {
    statusBarItem.tooltip = tooltip;
  }
}

async function refreshDiagnosticsAndNotify(
  context: vscode.ExtensionContext
): Promise<void> {
  const failures = await updateFailureDiagnostics(diagnosticCollection);
  const newFailures = failures.filter((f) => !seenFailureRunIds.has(f.runId));

  for (const failure of failures) {
    seenFailureRunIds.add(failure.runId);
  }

  if (newFailures.length === 0) {
    return;
  }

  const latest = newFailures[0];
  const view = "View Diagnosis";
  const selection = await vscode.window.showErrorMessage(
    `AgentAutopsy: ${latest.agentName} failed — ${latest.message}`,
    view
  );
  if (selection === view) {
    await openAgentAutopsyPanel(context);
  }
}

async function startWatching(context: vscode.ExtensionContext): Promise<void> {
  if (!(await ensureInstalled())) {
    return;
  }

  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.languageId !== "python") {
    vscode.window.showWarningMessage("Open a Python file to start watching.");
    return;
  }

  const text = editor.document.getText();
  if (!text.includes("agentautopsy.watch")) {
    const edit = new vscode.WorkspaceEdit();
    const watchBlock =
      "import agentautopsy\n\nagentautopsy.watch()\n\n";
    edit.insert(editor.document.uri, new vscode.Position(0, 0), watchBlock);
    const applied = await vscode.workspace.applyEdit(edit);
    if (applied) {
      await editor.document.save();
    }
  }

  watchingEnabled = true;
  updateStatusBar("🔬 AgentAutopsy: Watching", "AgentAutopsy is watching this workspace");
  vscode.window.showInformationMessage(
    "AgentAutopsy watching enabled. Run your agent, then save to refresh diagnostics."
  );
  await refreshDiagnosticsAndNotify(context);
}

async function fixRun(): Promise<void> {
  if (!(await ensureInstalled())) {
    return;
  }

  const failures = await updateFailureDiagnostics(diagnosticCollection);
  if (failures.length === 0) {
    vscode.window.showInformationMessage("No failed AgentAutopsy runs found.");
    return;
  }

  const pick = await vscode.window.showQuickPick(
    failures.map((failure) => ({
      label: failure.agentName,
      description: failure.message,
      detail: failure.runId,
      runId: failure.runId,
    })),
    { placeHolder: "Select a failed run to auto-fix" }
  );

  if (!pick) {
    return;
  }

  const terminal = vscode.window.createTerminal("AgentAutopsy Fix");
  terminal.show();
  terminal.sendText(`agentautopsy fix ${pick.runId}`);
}

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left,
    100
  );
  statusBarItem.command = "agentautopsy.showRuns";
  updateStatusBar("🔬 AgentAutopsy: Ready", "Open AgentAutopsy dashboard");
  statusBarItem.show();

  diagnosticCollection = vscode.languages.createDiagnosticCollection("agentautopsy");
  context.subscriptions.push(
    statusBarItem,
    diagnosticCollection,
    vscode.commands.registerCommand("agentautopsy.startWatching", () =>
      startWatching(context)
    ),
    vscode.commands.registerCommand("agentautopsy.showRuns", () =>
      openAgentAutopsyPanel(context)
    ),
    vscode.commands.registerCommand("agentautopsy.fixRun", fixRun),
    vscode.workspace.onDidSaveTextDocument(async (document) => {
      if (document.languageId !== "python") {
        return;
      }
      if (watchingEnabled || document.fileName.endsWith(".py")) {
        await refreshDiagnosticsAndNotify(context);
      }
    })
  );

  const installed = await isAgentAutopsyInstalled();
  if (!installed) {
    await ensureInstalled();
  } else {
    await refreshDiagnosticsAndNotify(context);
  }
}

export function deactivate(): void {
  diagnosticCollection?.clear();
  diagnosticCollection?.dispose();
}
