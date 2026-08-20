import express from "express";
import type { MockPaymentScenario } from "./xrplX402.js";

export interface MockFacilitatorOptions {
  payer?: string;
  txHash?: string;
  network?: string;
}

export function createMockXrplFacilitatorApp(options: MockFacilitatorOptions = {}) {
  const app = express();
  app.use(express.json({ limit: "1mb" }));

  const payer = options.payer ?? "rMockPayer";
  const txHash = options.txHash ?? "XRPL_MOCK_TX_HASH";
  const network = options.network ?? "xrpl:1";

  app.post("/verify", (req, res) => {
    const scenario = getScenario(req.body);
    if (scenario === "malformed") {
      return res.status(400).json({ error: "malformed mock XRPL payment payload" });
    }
    if (scenario === "verify_failed") {
      return res.json({ valid: false, invalidReason: "mock XRPL verify failed" });
    }
    if (scenario === "expired") {
      return res.json({ valid: false, invalidReason: "mock XRPL payment requirement expired" });
    }
    return res.json({ valid: true, payer, network });
  });

  app.post("/settle", (req, res) => {
    const scenario = getScenario(req.body);
    if (scenario === "malformed") {
      return res.status(400).json({ error: "malformed mock XRPL payment payload" });
    }
    if (scenario === "settle_failed") {
      return res.json({ settled: false, txHash: "", errorReason: "mock XRPL settle failed" });
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

