import express from "express";
import { fileURLToPath } from "node:url";
import { loadConfig, type AppConfig } from "./config.js";
import { createActionsRouter, handleActionError } from "./routes/actions.js";
import { createSkillsRouter } from "./routes/skills.js";
import type { Publisher } from "./publishers/Publisher.js";
import { StubPublisher } from "./publishers/StubPublisher.js";
import { ZenohCliPublisher } from "./publishers/ZenohCliPublisher.js";

export interface CreateAppOptions {
  config?: AppConfig;
  publisher?: Publisher;
}

export function createPublisher(config: AppConfig): Publisher {
  if (config.publisher === "zenoh-cli") {
    return new ZenohCliPublisher();
  }
  return new StubPublisher();
}

export function createApp(options: CreateAppOptions = {}) {
  const config = options.config ?? loadConfig();
  const publisher = options.publisher ?? createPublisher(config);
  const app = express();

  app.use(express.json({ limit: "1mb" }));
  app.get("/health", (_req, res) => res.json({ ok: true, publisher: config.publisher, robotId: config.robotId }));
  app.use(createSkillsRouter(config));
  app.use(createActionsRouter({ config, publisher }));
  app.use(handleActionError);
  app.use((error: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    const message = error instanceof Error ? error.message : "Unknown error";
    res.status(500).json({ error: "INTERNAL_ERROR", message });
  });

  return { app, config, publisher };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const { app, config, publisher } = createApp();
  if (config.publisher === "stub") {
    console.warn("[aeon-robot-communication] PUBLISHER=stub: Zenoh publish is not real in this runtime.");
  }
  app.listen(config.port, () => {
    console.log(
      `[aeon-robot-communication] gateway listening on http://127.0.0.1:${config.port} robotId=${config.robotId} publisher=${config.publisher} topic=${config.zenohTopic}`
    );
    if (publisher instanceof StubPublisher) {
      console.log("[aeon-robot-communication] StubPublisher active; payloads are captured in memory only.");
    }
  });
}
