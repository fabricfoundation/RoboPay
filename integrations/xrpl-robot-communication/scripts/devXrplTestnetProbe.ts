import dotenv from "dotenv";
import express from "express";

dotenv.config();

const { requirePayment } = await import("x402-xrpl/express");

const port = Number.parseInt(process.env.XRPL_TESTNET_PROBE_PORT ?? "4022", 10);
const endpointPath = process.env.XRPL_TESTNET_ENDPOINT_PATH ?? "/xrpl-testnet/payment-only";
const facilitatorUrl = process.env.XRPL_FACILITATOR_URL ?? "https://xrpl-facilitator-testnet.t54.ai";
const network = process.env.XRPL_NETWORK ?? "xrpl:1";
const payToAddress = process.env.XRPL_PAY_TO;
const price = process.env.XRPL_AMOUNT ?? process.env.XRPL_PRICE_DROPS ?? "1000";
const asset = process.env.XRPL_ASSET ?? "XRP";
const sourceTag = Number.parseInt(process.env.XRPL_SOURCE_TAG ?? "20260601", 10);

if (!payToAddress) {
  throw new Error("XRPL_PAY_TO is required for the XRPL testnet payment-only probe.");
}

const app = express();
app.use(express.json({ limit: "1mb" }));

app.get("/health", (_req, res) =>
  res.json({
    ok: true,
    mode: "xrpl-testnet-payment-only",
    endpointPath,
    facilitatorUrl,
    network,
    asset,
    price,
    payToAddress,
    sourceTag
  })
);

app.use(
  requirePayment({
    path: endpointPath,
    price,
    payToAddress,
    network,
    facilitatorUrl,
    asset,
    resource: "paid:xrpl-robot-payment-only-probe",
    description: "XRPL x402 testnet payment-only probe; does not publish Zenoh or execute robot actions.",
    extra: { sourceTag }
  }) as express.RequestHandler
);

app.get(endpointPath, (_req, res) =>
  res.json({
    success: true,
    mode: "xrpl-testnet-payment-only",
    message: "XRPL x402 testnet payment was verified and settled. No robot action was dispatched.",
    robotActionDispatched: false,
    zenohPublished: false,
    configuredPayment: {
      network,
      asset,
      amount: price,
      amountUnit: asset === "XRP" ? "drops" : "decimal",
      payTo: payToAddress,
      sourceTag
    }
  })
);

app.listen(port, () => {
  console.log("[xrpl-testnet-probe] Starting XRPL testnet payment-only resource server.");
  console.log(
    JSON.stringify(
      {
        url: `http://127.0.0.1:${port}${endpointPath}`,
        facilitatorUrl,
        network,
        asset,
        price,
        payToAddress,
        sourceTag
      },
      null,
      2
    )
  );
  console.log("[xrpl-testnet-probe] This endpoint does not publish Zenoh and does not execute robot actions.");
});

