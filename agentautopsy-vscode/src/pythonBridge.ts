import { execFile } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

export function getWorkspaceRoot(): string | undefined {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    return undefined;
  }
  return folders[0].uri.fsPath;
}

export function getDbPath(root: string): string {
  return path.join(root, "agentautopsy.db");
}

function pythonEnv(root: string): NodeJS.ProcessEnv {
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

export function runPythonJson<T>(
  root: string,
  code: string,
  timeoutMs = 30000
): Promise<T> {
  return new Promise((resolve, reject) => {
    const args = ["-c", code];
    execFile(
      "python",
      args,
      { cwd: root, maxBuffer: 10 * 1024 * 1024, timeout: timeoutMs, env: pythonEnv(root) },
      (error, stdout, stderr) => {
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
          resolve(JSON.parse(text) as T);
        } catch (parseError) {
          reject(
            new Error(
              `Failed to parse Python JSON: ${String(parseError)}\n${text}`
            )
          );
        }
      }
    );
  });
}

export async function isAgentAutopsyInstalled(): Promise<boolean> {
  return new Promise((resolve) => {
    execFile(
      "pip",
      ["show", "agentautopsy"],
      { timeout: 15000 },
      (error) => resolve(!error)
    );
  });
}
