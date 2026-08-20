import { spawn } from "node:child_process";
import type { Publisher } from "./Publisher.js";

export class ZenohCliPublisher implements Publisher {
  async publish(topic: string, payload: unknown): Promise<void> {
    const json = JSON.stringify(payload);
    await runZenohPublish(["pub", "-k", topic, "-v", json]);
  }
}

function runZenohPublish(args: string[]): Promise<void> {
  const pythonWrapper = process.env.ZENOH_PYTHON_WRAPPER;
  if (pythonWrapper) {
    return runCommand(process.env.ZENOH_PYTHON ?? "python", [pythonWrapper, ...args], "zenoh Python wrapper");
  }

  return runCommand(process.env.ZENOH_CLI_PATH ?? "zenoh", args, "zenoh CLI");
}

function runCommand(command: string, args: string[], label: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { shell: false, stdio: ["ignore", "pipe", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += String(chunk);
    });
    child.on("error", (error: NodeJS.ErrnoException) => {
      if (error.code === "ENOENT") {
        reject(
          new Error(
            `${label} is unavailable; install zenoh on Ubuntu/OM1, set ZENOH_CLI_PATH, set ZENOH_PYTHON_WRAPPER, or use PUBLISHER=stub for local proof.`,
          ),
        );
        return;
      }
      reject(error);
    });
    child.on("close", (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(`${label} publish failed with exit code ${code}: ${stderr.trim()}`));
    });
  });
}
