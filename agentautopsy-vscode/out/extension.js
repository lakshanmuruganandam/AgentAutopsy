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
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const diagnostics_1 = require("./diagnostics");
const panel_1 = require("./panel");
const pythonBridge_1 = require("./pythonBridge");
let statusBarItem;
let diagnosticCollection;
const seenFailureRunIds = new Set();
let watchingEnabled = false;
async function ensureInstalled() {
    const installed = await (0, pythonBridge_1.isAgentAutopsyInstalled)();
    if (installed) {
        return true;
    }
    const install = "Install AgentAutopsy";
    const selection = await vscode.window.showInformationMessage("AgentAutopsy is not installed.", install);
    if (selection === install) {
        const terminal = vscode.window.createTerminal("AgentAutopsy Install");
        terminal.show();
        terminal.sendText("pip install agentautopsy");
    }
    return false;
}
function updateStatusBar(text, tooltip) {
    statusBarItem.text = text;
    if (tooltip) {
        statusBarItem.tooltip = tooltip;
    }
}
async function refreshDiagnosticsAndNotify(context) {
    const failures = await (0, diagnostics_1.updateFailureDiagnostics)(diagnosticCollection);
    const newFailures = failures.filter((f) => !seenFailureRunIds.has(f.runId));
    for (const failure of failures) {
        seenFailureRunIds.add(failure.runId);
    }
    if (newFailures.length === 0) {
        return;
    }
    const latest = newFailures[0];
    const view = "View Diagnosis";
    const selection = await vscode.window.showErrorMessage(`AgentAutopsy: ${latest.agentName} failed — ${latest.message}`, view);
    if (selection === view) {
        await (0, panel_1.openAgentAutopsyPanel)(context);
    }
}
async function startWatching(context) {
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
        const watchBlock = "import agentautopsy\n\nagentautopsy.watch()\n\n";
        edit.insert(editor.document.uri, new vscode.Position(0, 0), watchBlock);
        const applied = await vscode.workspace.applyEdit(edit);
        if (applied) {
            await editor.document.save();
        }
    }
    watchingEnabled = true;
    updateStatusBar("🔬 AgentAutopsy: Watching", "AgentAutopsy is watching this workspace");
    vscode.window.showInformationMessage("AgentAutopsy watching enabled. Run your agent, then save to refresh diagnostics.");
    await refreshDiagnosticsAndNotify(context);
}
async function fixRun() {
    if (!(await ensureInstalled())) {
        return;
    }
    const failures = await (0, diagnostics_1.updateFailureDiagnostics)(diagnosticCollection);
    if (failures.length === 0) {
        vscode.window.showInformationMessage("No failed AgentAutopsy runs found.");
        return;
    }
    const pick = await vscode.window.showQuickPick(failures.map((failure) => ({
        label: failure.agentName,
        description: failure.message,
        detail: failure.runId,
        runId: failure.runId,
    })), { placeHolder: "Select a failed run to auto-fix" });
    if (!pick) {
        return;
    }
    const terminal = vscode.window.createTerminal("AgentAutopsy Fix");
    terminal.show();
    terminal.sendText(`agentautopsy fix ${pick.runId}`);
}
async function activate(context) {
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.command = "agentautopsy.showRuns";
    updateStatusBar("🔬 AgentAutopsy: Ready", "Open AgentAutopsy dashboard");
    statusBarItem.show();
    diagnosticCollection = vscode.languages.createDiagnosticCollection("agentautopsy");
    context.subscriptions.push(statusBarItem, diagnosticCollection, vscode.commands.registerCommand("agentautopsy.startWatching", () => startWatching(context)), vscode.commands.registerCommand("agentautopsy.showRuns", () => (0, panel_1.openAgentAutopsyPanel)(context)), vscode.commands.registerCommand("agentautopsy.fixRun", fixRun), vscode.workspace.onDidSaveTextDocument(async (document) => {
        if (document.languageId !== "python") {
            return;
        }
        if (watchingEnabled || document.fileName.endsWith(".py")) {
            await refreshDiagnosticsAndNotify(context);
        }
    }));
    const installed = await (0, pythonBridge_1.isAgentAutopsyInstalled)();
    if (!installed) {
        await ensureInstalled();
    }
    else {
        await refreshDiagnosticsAndNotify(context);
    }
}
function deactivate() {
    diagnosticCollection?.clear();
    diagnosticCollection?.dispose();
}
//# sourceMappingURL=extension.js.map