import dotenv from "dotenv";
import { Wallet } from "xrpl";
import { decodePaymentRequiredHeader, decodePaymentResponseHeader, x402Purchase } from "x402-xrpl";

dotenv.config();

const baseUrl = process.env.GATEWAY_URL ?? "http://127.0.0.1:18080";
const robotId = process.env.ROBOT_ID ?? "g1-demo-001";
const skillId = process.env.SKILL_ID ?? "move_forward";
const idempotencyKey = process.env.IDEMPOTENCY_KEY ?? `xrpl-testnet-action-${Date.now()}`;
const seed = process.env.XRPL_BUYER_SEED;
const network = (process.env.XRPL_NETWORK ?? "xrpl:1") as "xrpl:0" | "xrpl:1" | "xrpl:2";
const wsUrl = process.env.XRPL_TESTNET_WS_URL ?? "wss://s.altnet.rippletest.net:51233";
const actionBody = { skillId, params: parseParams(skillId), idempotencyKey };
const actionUrl = `${baseUrl}/v1/robots/${robotId}/actions`;

if (!seed) {
  throw new Error("XRPL_BUYER_SEED is required for XRPL testnet paid action.");
}

const wallet = Wallet.fromSeed(seed);
console.log(
  JSON.stringify(
    {
      step: "config",
      gatewayUrl: baseUrl,
      robotId,
      skillId,
      idempotencyKey,
      payer: wallet.classicAddress,
      network,
      wsUrl
    },
    null,
    2
  )
);

const skills = await getJson(`${baseUrl}/v1/robots/${robotId}/skills`);
console.log(JSON.stringify({ step: "skills", status: skills.status, body: skills.body }, null, 2));

const unpaid = await postJson(actionUrl, actionBody);
console.log(
  JSON.stringify(
    {
      step: "unpaid_probe",
      status: unpaid.status,
      paymentRequiredHeaderPresent: Boolean(unpaid.headers.get("PAYMENT-REQUIRED")),
      paymentRequired: summarizePaymentRequired(unpaid.headers.get("PAYMENT-REQUIRED")),
      body: unpaid.body
    },
    null,
    2
  )
);
if (unpaid.status !== 402) {
  throw new Error(`Expected unpaid action to return 402. Got ${unpaid.status}.`);
}

const purchase = await x402Purchase({
  url: actionUrl,
  method: "POST",
  headers: { "content-type": "application/json", accept: "application/json" },
  body: JSON.stringify(actionBody),
  wallet,
  network,
  wsUrl
});

const paidBody = purchase.response ? await parseResponseBody(purchase.response) : null;
console.log(
  JSON.stringify(
    {
      step: "paid_action",
      status: purchase.response?.status,
      purchaseStatus: purchase.status,
      transaction: purchase.transaction,
      network: purchase.network,
      payer: purchase.payer,
      paymentResponse: purchase.paymentResponse,
      body: paidBody
    },
    null,
    2
  )
);
if (purchase.status !== "success" || !purchase.response?.ok) {
  throw new Error(`XRPL testnet paid action failed: ${purchase.status} ${purchase.reason ?? ""}`);
}

const duplicate = await postJson(actionUrl, actionBody);
console.log(JSON.stringify({ step: "duplicate", status: duplicate.status, body: duplicate.body }, null, 2));

const modified = await postJson(actionUrl, { ...actionBody, params: modifiedParams(skillId) });
console.log(JSON.stringify({ step: "modifiedParams", status: modified.status, body: modified.body }, null, 2));

const wrongRobot = await postJson(`${baseUrl}/v1/robots/wrong-robot/actions`, actionBody);
console.log(JSON.stringify({ step: "wrongRobot", status: wrongRobot.status, body: wrongRobot.body }, null, 2));

const wrongSkill = await postJson(`${baseUrl}/v1/robots/${robotId}/actions`, {
  skillId: "wave",
  params: {},
  idempotencyKey: `xrpl-testnet-wrong-${Date.now()}`
});
console.log(JSON.stringify({ step: "wrongSkill", status: wrongSkill.status, body: wrongSkill.body }, null, 2));

if (duplicate.status !== 200 || duplicate.body?.published !== false || modified.status !== 409 || wrongRobot.status !== 404 || wrongSkill.status !== 404) {
  throw new Error("XRPL testnet paid action post-checks failed.");
}

function parseParams(currentSkillId: string): Record<string, unknown> {
  if (process.env.ACTION_PARAMS) {
    return JSON.parse(process.env.ACTION_PARAMS) as Record<string, unknown>;
  }
  if (currentSkillId === "stop") return {};
  if (currentSkillId === "turn_left" || currentSkillId === "turn_right") {
    return { durationSec: 2, angularSpeed: 0.4 };
  }
  return { durationSec: 3, speed: 0.5 };
}

function modifiedParams(currentSkillId: string): Record<string, unknown> {
  if (currentSkillId === "stop") return { unexpected: true };
  if (currentSkillId === "turn_left" || currentSkillId === "turn_right") {
    return { durationSec: 1, angularSpeed: 0.2 };
  }
  return { durationSec: 2, speed: 0.4 };
}

async function getJson(url: string) {
  const response = await fetch(url);
  return { status: response.status, body: await response.json() };
}

async function postJson(url: string, body: unknown) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body)
  });
  return { status: response.status, headers: response.headers, body: await response.json() };
}

async function parseResponseBody(response: Response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function summarizePaymentRequired(header: string | null) {
  if (!header) return undefined;
  const decoded = decodePaymentRequiredHeader(header);
  return {
    x402Version: decoded.x402Version,
    resource: decoded.resource,
    accepts: decoded.accepts.map((accept) => ({
      scheme: accept.scheme,
      network: accept.network,
      amount: accept.amount,
      asset: accept.asset,
      payTo: accept.payTo,
      maxTimeoutSeconds: accept.maxTimeoutSeconds,
      extra: accept.extra
    }))
  };
}

export function summarizePaymentResponse(header: string | null) {
  if (!header) return undefined;
  return decodePaymentResponseHeader(header);
}

