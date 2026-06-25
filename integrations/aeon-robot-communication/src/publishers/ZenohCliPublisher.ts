import { spawn } from "node:child_process";
import type { Publisher } from "./Publisher.js";

export class ZenohCliPublisher implements Publisher {
  async publish(topic: string, payload: unknown): Promise<void> {
    const json = JSON.stringify(payload);
    await runZenoh(["pub", "-k", topic, "-v", json]);
  }
}

function runZenoh(args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn("zenoh", args, { shell: false, stdio: ["ignore", "pipe", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += String(chunk);
    });
    child.on("error", (error: NodeJS.ErrnoException) => {
      if (error.code === "ENOENT") {
        reject(new Error("zenoh CLI is unavailable; install zenoh on Ubuntu/OM1 or set PUBLISHER=stub for local Windows proof."));
        return;
      }
      reject(error);
    });
    child.on("close", (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(`zenoh CLI publish failed with exit code ${code}: ${stderr.trim()}`));
    });
  });
}
