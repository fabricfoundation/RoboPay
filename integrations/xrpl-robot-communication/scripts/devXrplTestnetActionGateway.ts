import express from "express";
import { fileURLToPath } from "node:url";
import { loadConfig } from "../src/config.js";
import { StubPublisher } from "../src/publishers/StubPublisher.js";
import { ZenohCliPublisher } from "../src/publishers/ZenohCliPublisher.js";
import { createSkillsRouter } from "../src/routes/skills.js";
import { createXrplTestnetActionsRouter, handleXrplTestnetActionError } from "../src/routes/xrplTestnetActions.js";

const config = loadConfig({
  facilitatorUrl: process.env.XRPL_FACILITATOR_URL ?? "https://xrpl-facilitator-testnet.t54.ai",
  network: process.env.XRPL_NETWORK ?? "xrpl:1",
  asset: process.env.XRPL_ASSET ?? "XRP",
  amount: process.env.XRPL_AMOUNT ?? process.env.XRPL_PRICE_DROPS ?? "1000",
  amountUnit: process.env.XRPL_AMOUNT_UNIT ?? "drops"
});
const publisher = config.publisher === "zenoh-cli" ? new ZenohCliPublisher() : new StubPublisher();
const app = express();

app.use(express.json({ limit: "1mb" }));
app.get("/health", (_req, res) =>
  res.json({
    ok: true,
    mode: "xrpl-testnet-actions",
    provider: config.paymentProvider,
    publisher: config.publisher,
    robotId: config.robotId,
    network: config.network,
    asset: config.asset,
    amount: config.amount,
    payTo: config.payTo,
    facilitatorUrl: config.facilitatorUrl
  })
);
app.use(createSkillsRouter(config));
app.use(createXrplTestnetActionsRouter({ config, publisher }));
app.use(handleXrplTestnetActionError);
app.use((error: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  const message = error instanceof Error ? error.message : "Unknown error";
  res.status(500).json({ error: "INTERNAL_ERROR", message });
});

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  if (config.publisher === "stub") {
    console.warn("[xrpl-testnet-actions] PUBLISHER=stub: Zenoh publish is not real in this runtime.");
  }
  app.listen(config.port, () => {
    console.log(
      `[xrpl-testnet-actions] listening on http://127.0.0.1:${config.port} robotId=${config.robotId} publisher=${config.publisher} topic=${config.zenohTopic}`
    );
    console.log(
      JSON.stringify(
        {
          facilitatorUrl: config.facilitatorUrl,
          network: config.network,
          asset: config.asset,
          amount: config.amount,
          amountUnit: config.amountUnit,
          payTo: config.payTo,
          sourceTag: config.sourceTag
        },
        null,
        2
      )
    );
  });
}

