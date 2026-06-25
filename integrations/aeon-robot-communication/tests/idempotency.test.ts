import { afterEach, describe, expect, test } from "vitest";
import { defaultAction, getPaymentRequirement, paymentHeader, postJson, startTestStack, type TestStack } from "./helpers.js";

let stack: TestStack | undefined;

afterEach(async () => {
  await stack?.close();
  stack = undefined;
});

describe("idempotency", () => {
  test("duplicate idempotencyKey with same action returns cached response and does not republish", async () => {
    stack = await startTestStack();
    const { requirement } = await getPaymentRequirement(stack.baseUrl);
    const first = await postJson(stack.baseUrl, "/v1/robots/g1-demo-001/actions", defaultAction, paymentHeader(requirement));
    const second = await postJson(stack.baseUrl, "/v1/robots/g1-demo-001/actions", defaultAction, paymentHeader(requirement));

    expect(first.response.status).toBe(200);
    expect(second.response.status).toBe(200);
    expect(second.body.actionId).toBe(first.body.actionId);
    expect(second.body.published).toBe(false);
    expect(stack.publisher.messages).toHaveLength(1);
  });

  test("duplicate idempotencyKey with modified params is rejected before another publish", async () => {
    stack = await startTestStack();
    const { requirement } = await getPaymentRequirement(stack.baseUrl);
    const first = await postJson(stack.baseUrl, "/v1/robots/g1-demo-001/actions", defaultAction, paymentHeader(requirement));
    const modified = await postJson(
      stack.baseUrl,
      "/v1/robots/g1-demo-001/actions",
      { ...defaultAction, params: { durationSec: 2, speed: 0.4 } },
      paymentHeader(requirement)
    );

    expect(first.response.status).toBe(200);
    expect(modified.response.status).toBe(409);
    expect(modified.body.error).toBe("IDEMPOTENCY_CONFLICT");
    expect(stack.publisher.messages).toHaveLength(1);
  });
});
