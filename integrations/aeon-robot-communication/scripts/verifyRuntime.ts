import { createMockPaymentPayload } from "../src/payment/aeonX402.js";
import type { PaymentRequirement } from "../src/payment/paymentRequirement.js";

const baseUrl = process.env.GATEWAY_URL ?? "http://127.0.0.1:18080";
const robotId = process.env.ROBOT_ID ?? "g1-demo-001";
const action = {
  skillId: "move_forward",
  params: { durationSec: 3, speed: 0.5 },
  idempotencyKey: `aeon-runtime-${Date.now()}`
};

const skills = await getJson(`/v1/robots/${robotId}/skills`);
console.log(JSON.stringify({ check: "skills", status: skills.status, body: skills.body }, null, 2));

const unpaid = await postJson(`/v1/robots/${robotId}/actions`, action);
console.log(JSON.stringify({ check: "unpaid", status: unpaid.status, body: unpaid.body }, null, 2));
if (unpaid.status !== 402 || !unpaid.body.paymentRequired) {
  throw new Error("Expected unpaid request to return 402.");
}

const paymentSignature = createMockPaymentPayload(unpaid.body.paymentRequired as PaymentRequirement);
const paid = await postJson(`/v1/robots/${robotId}/actions`, action, { "payment-signature": paymentSignature });
console.log(JSON.stringify({ check: "paid", status: paid.status, body: paid.body }, null, 2));

const duplicate = await postJson(`/v1/robots/${robotId}/actions`, action, { "payment-signature": paymentSignature });
console.log(JSON.stringify({ check: "duplicate", status: duplicate.status, body: duplicate.body }, null, 2));

const modified = await postJson(
  `/v1/robots/${robotId}/actions`,
  { ...action, params: { durationSec: 2, speed: 0.4 } },
  { "payment-signature": paymentSignature }
);
console.log(JSON.stringify({ check: "modifiedParams", status: modified.status, body: modified.body }, null, 2));

const wrongRobot = await postJson(`/v1/robots/wrong-robot/actions`, action, { "payment-signature": paymentSignature });
console.log(JSON.stringify({ check: "wrongRobot", status: wrongRobot.status, body: wrongRobot.body }, null, 2));

const wrongSkill = await postJson(
  `/v1/robots/${robotId}/actions`,
  { skillId: "wave", params: {}, idempotencyKey: `aeon-runtime-wrong-${Date.now()}` },
  { "payment-signature": paymentSignature }
);
console.log(JSON.stringify({ check: "wrongSkill", status: wrongSkill.status, body: wrongSkill.body }, null, 2));

if (!paid.ok || duplicate.status !== 200 || modified.status !== 409 || wrongRobot.status !== 404 || wrongSkill.status !== 404) {
  throw new Error("Runtime verification failed.");
}

async function getJson(path: string) {
  const response = await fetch(`${baseUrl}${path}`);
  return { status: response.status, ok: response.ok, body: await response.json() };
}

async function postJson(path: string, body: unknown, headers: Record<string, string> = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body)
  });
  return { status: response.status, ok: response.ok, body: await response.json() };
}
