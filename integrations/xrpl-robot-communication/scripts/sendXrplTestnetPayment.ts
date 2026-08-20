import dotenv from "dotenv";
import { Wallet } from "xrpl";
import { decodePaymentRequiredHeader, decodePaymentResponseHeader, x402Fetch } from "x402-xrpl";

dotenv.config();

const resourceUrl =
  process.env.RESOURCE_URL ??
  `${process.env.XRPL_TESTNET_RESOURCE_SERVER_URL ?? "http://127.0.0.1:4022"}${
    process.env.XRPL_TESTNET_ENDPOINT_PATH ?? "/xrpl-testnet/payment-only"
  }`;
const network = (process.env.XRPL_NETWORK ?? "xrpl:1") as "xrpl:0" | "xrpl:1" | "xrpl:2";
const seed = process.env.XRPL_BUYER_SEED;
const faucetUrl = process.env.XRPL_TESTNET_FAUCET_URL ?? "https://faucet.altnet.rippletest.net/accounts";
const skipFaucet = ["1", "true", "yes"].includes(String(process.env.XRPL_SKIP_FAUCET ?? "false").toLowerCase());

if (!seed) {
  throw new Error("XRPL_BUYER_SEED is required for XRPL testnet payment.");
}

const buyer = Wallet.fromSeed(seed);
console.log(`[xrpl-testnet-client] Buyer: ${buyer.classicAddress}`);
console.log(`[xrpl-testnet-client] Resource URL: ${resourceUrl}`);
console.log(`[xrpl-testnet-client] Network guard: ${network}`);

await fundWalletIfNeeded(buyer.classicAddress);

const fetchPaid = x402Fetch({
  wallet: buyer,
  network
});

const response = await fetchPaid(resourceUrl, {
  method: "GET",
  headers: { accept: "application/json" }
});

const text = await response.text();
console.log(JSON.stringify({ step: "paid_request", status: response.status, ok: response.ok, body: parseMaybeJson(text) }, null, 2));

const paymentResponse = response.headers.get("PAYMENT-RESPONSE");
if (paymentResponse) {
  console.log("[xrpl-testnet-client] Decoded PAYMENT-RESPONSE:");
  console.log(JSON.stringify(decodePaymentResponseHeader(paymentResponse), null, 2));
}

const paymentRequired = response.headers.get("PAYMENT-REQUIRED");
if (paymentRequired) {
  console.log("[xrpl-testnet-client] Decoded PAYMENT-REQUIRED:");
  console.log(JSON.stringify(decodePaymentRequiredHeader(paymentRequired), null, 2));
}

if (!response.ok) {
  throw new Error(`XRPL testnet paid request failed with status ${response.status}.`);
}

async function fundWalletIfNeeded(address: string) {
  if (skipFaucet) return;
  if (network !== "xrpl:1") return;

  console.log(`[xrpl-testnet-client] Funding buyer wallet via faucet if needed: ${address}`);
  const response = await fetch(faucetUrl, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ destination: address })
  });
  console.log(`[xrpl-testnet-client] Faucet response: ${response.status}`);
  await sleep(10_000);
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parseMaybeJson(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}
