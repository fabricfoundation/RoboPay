import { afterEach, describe, expect, test } from "vitest";
import { createMockPaymentPayload } from "../src/payment/aeonX402.js";
import { defaultAction, getPaymentRequirement, paymentHeader, postJson, startTestStack, xPaymentHeader, type TestStack } from "./helpers.js";

let stack: TestStack | undefined;

afterEach(async () => {
  await stack?.close();
  stack = undefined;
});

describe("paid action flow", () => {
  test("unpaid action returns 402 with AEON V2 payment requirement binding", async () => {
    stack = await startTestStack();
    const { response, requirement } = await getPaymentRequirement(stack.baseUrl);

    expect(response.status).toBe(402);
    expect(response.headers.get("payment-required")).toBeTruthy();
    expect(requirement).toMatchObject({
      scheme: "exact",
      network: "eip155:56",
      amount: "10000",
      asset: "USDT_OR_USDC_CONTRACT",
      payTo: "0x0000000000000000000000000000000000000001"
    });
    expect(requirement.extra).toMatchObject({
      robotId: "g1-demo-001",
      skillId: "move_forward",
      idempotencyKey: "aeon-local-001",
      resource: "/v1/robots/g1-demo-001/actions",
      amount: "10000",
      asset: "USDT_OR_USDC_CONTRACT",
      network: "eip155:56",
      payTo: "0x0000000000000000000000000000000000000001"
    });
    expect(requirement.extra.paramsHash).toMatch(/^sha256\([a-f0-9]{64}\)$/);
  });

  test("paid action verifies, settles, emits receipt, and publishes one envelope", async () => {
    stack = await startTestStack();
    const { requirement } = await getPaymentRequirement(stack.baseUrl);
    const { response, body } = await postJson(
      stack.baseUrl,
      "/v1/robots/g1-demo-001/actions",
      defaultAction,
      paymentHeader(requirement)
    );

    expect(response.status).toBe(200);
    expect(body.status).toBe("accepted");
    expect(body.published).toBe(true);
    expect(body.actionId).toMatch(/^act_/);
    expect(body.paymentReceipt).toMatchObject({
      provider: "aeon-bnb-x402",
      txHash: "0xmocktx",
      payer: "0xMockPayer",
      payTo: "0x0000000000000000000000000000000000000001",
      amount: "10000",
      asset: "USDT_OR_USDC_CONTRACT",
      network: "eip155:56",
      robotId: "g1-demo-001",
      skillId: "move_forward",
      idempotencyKey: "aeon-local-001",
      resource: "/v1/robots/g1-demo-001/actions"
    });
    expect(stack.publisher.messages).toHaveLength(1);
    expect(stack.publisher.messages[0]).toMatchObject({
      topic: "robot/tunnel/action",
      payload: {
        actionId: body.actionId,
        robotId: "g1-demo-001",
        skillId: "move_forward",
        params: { durationSec: 3, speed: 0.5 },
        idempotencyKey: "aeon-local-001",
        paramsHash: requirement.extra.paramsHash,
        payment: {
          provider: "aeon-bnb-x402",
          txHash: "0xmocktx",
          payer: "0xMockPayer",
          payTo: "0x0000000000000000000000000000000000000001",
          amount: "10000",
          asset: "USDT_OR_USDC_CONTRACT",
          network: "eip155:56"
        }
      }
    });
  });

  test("accepts X-PAYMENT compatibility header", async () => {
    stack = await startTestStack();
    const action = { ...defaultAction, idempotencyKey: "aeon-local-x-payment" };
    const { requirement } = await getPaymentRequirement(stack.baseUrl, action);
    const { response } = await postJson(stack.baseUrl, "/v1/robots/g1-demo-001/actions", action, xPaymentHeader(requirement));

    expect(response.status).toBe(200);
    expect(stack.publisher.messages).toHaveLength(1);
  });

  test("accepts JSON payment-signature payload as well as base64 JSON", async () => {
    stack = await startTestStack();
    const action = { ...defaultAction, idempotencyKey: "aeon-local-json-payment" };
    const { requirement } = await getPaymentRequirement(stack.baseUrl, action);
    const payload = Buffer.from(createMockPaymentPayload(requirement), "base64").toString("utf8");
    const { response } = await postJson(stack.baseUrl, "/v1/robots/g1-demo-001/actions", action, { "payment-signature": payload });

    expect(response.status).toBe(200);
    expect(stack.publisher.messages).toHaveLength(1);
  });
});
