import express from "express";
import type { MockPaymentScenario } from "./aeonX402.js";

export interface MockFacilitatorOptions {
  payer?: string;
  txHash?: string;
  network?: string;
}

export function createMockFacilitatorApp(options: MockFacilitatorOptions = {}) {
  const app = express();
  app.use(express.json({ limit: "1mb" }));

  const payer = options.payer ?? "0xMockPayer";
  const txHash = options.txHash ?? "0xmocktx";
  const network = options.network ?? "eip155:56";

  app.post("/verify", (req, res) => {
    const scenario = getScenario(req.body);
    if (scenario === "malformed") {
      return res.status(400).json({ error: "malformed mock payment payload" });
    }
    if (scenario === "verify_failed") {
      return res.json({ valid: false, invalidReason: "mock verify failed" });
    }
    if (scenario === "expired") {
      return res.json({ valid: false, invalidReason: "mock payment requirement expired" });
    }
    return res.json({ valid: true, payer, network });
  });

  app.post("/settle", (req, res) => {
    const scenario = getScenario(req.body);
    if (scenario === "malformed") {
      return res.status(400).json({ error: "malformed mock payment payload" });
    }
    if (scenario === "settle_failed") {
      return res.json({ settled: false, errorReason: "mock settle failed" });
    }
    return res.json({ settled: true, txHash, payer, network });
  });

  return app;
}

function getScenario(body: unknown): MockPaymentScenario | undefined {
  const paymentPayload = (body as { paymentPayload?: { scenario?: MockPaymentScenario; payload?: { scenario?: MockPaymentScenario } } })
    ?.paymentPayload;
  return paymentPayload?.scenario ?? paymentPayload?.payload?.scenario;
}
