import { createMockPaymentPayload } from "../src/payment/aeonX402.js";
import type { PaymentRequirement } from "../src/payment/paymentRequirement.js";

const baseUrl = process.env.GATEWAY_URL ?? "http://127.0.0.1:18080";
const robotId = process.env.ROBOT_ID ?? "g1-demo-001";
const skillId = process.env.SKILL_ID ?? "move_forward";
const idempotencyKey = process.env.IDEMPOTENCY_KEY ?? `aeon-local-${Date.now()}`;
const params = parseParams(skillId);

const actionBody = { skillId, params, idempotencyKey };
const unpaid = await fetch(`${baseUrl}/v1/robots/${robotId}/actions`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(actionBody)
});

const unpaidBody = (await unpaid.json()) as { paymentRequired?: PaymentRequirement };
console.log(JSON.stringify({ step: "unpaid", status: unpaid.status, body: unpaidBody }, null, 2));

if (unpaid.status !== 402 || !unpaidBody.paymentRequired) {
  throw new Error("Expected unpaid request to return 402 with paymentRequired.");
}

const paymentSignature = createMockPaymentPayload(unpaidBody.paymentRequired);
const paid = await fetch(`${baseUrl}/v1/robots/${robotId}/actions`, {
  method: "POST",
  headers: {
    "content-type": "application/json",
    "payment-signature": paymentSignature
  },
  body: JSON.stringify(actionBody)
});

const paidBody = await paid.json();
console.log(JSON.stringify({ step: "paid", status: paid.status, body: paidBody }, null, 2));

if (!paid.ok) {
  throw new Error(`Paid action failed with status ${paid.status}.`);
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
