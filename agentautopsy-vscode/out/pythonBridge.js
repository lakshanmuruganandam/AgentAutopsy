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
exports.getWorkspaceRoot = getWorkspaceRoot;
exports.getDbPath = getDbPath;
exports.runPythonJson = runPythonJson;
exports.isAgentAutopsyInstalled = isAgentAutopsyInstalled;
const child_process_1 = require("child_process");
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
const vscode = __importStar(require("vscode"));
function getWorkspaceRoot() {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) {
        return undefined;
    }
    return folders[0].uri.fsPath;
}
function getDbPath(root) {
    return path.join(root, "agentautopsy.db");
}
function pythonEnv(root) {
    const env = { ...process.env };
    const srcPath = path.join(root, "src");
    if (fs.existsSync(path.join(srcPath, "agentautopsy"))) {
        const existing = env.PYTHONPATH;
        env.PYTHONPATH = existing
            ? `${srcPath}${path.delimiter}${existing}`
            : srcPath;
    }
    return env;
}
function runPythonJson(root, code, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
        const args = ["-c", code];
        (0, child_process_1.execFile)("python", args, { cwd: root, maxBuffer: 10 * 1024 * 1024, timeout: timeoutMs, env: pythonEnv(root) }, (error, stdout, stderr) => {
            if (error) {
                reject(new Error(stderr || error.message));
                return;
            }
            const text = stdout.trim();
            if (!text) {
                reject(new Error("Python returned no output"));
                return;
            }
            try {
                resolve(JSON.parse(text));
            }
            catch (parseError) {
                reject(new Error(`Failed to parse Python JSON: ${String(parseError)}\n${text}`));
            }
        });
    });
}
async function isAgentAutopsyInstalled() {
    return new Promise((resolve) => {
        (0, child_process_1.execFile)("pip", ["show", "agentautopsy"], { timeout: 15000 }, (error) => resolve(!error));
    });
}
//# sourceMappingURL=pythonBridge.js.map