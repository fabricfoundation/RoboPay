import { afterEach, describe, expect, test } from "vitest";
import { defaultAction, getPaymentRequirement, paymentHeader, postJson, startTestStack, type TestStack } from "./helpers.js";

let stack: TestStack | undefined;

afterEach(async () => {
  await stack?.close();
  stack = undefined;
});

describe("payment binding and failure paths", () => {
  test("verify failed does not publish", async () => {
    stack = await startTestStack();
    const action = { ...defaultAction, idempotencyKey: "verify-failed" };
    const { requirement } = await getPaymentRequirement(stack.baseUrl, action);
    const { response, body } = await postJson(stack.baseUrl, "/v1/robots/g1-demo-001/actions", action, paymentHeader(requirement, "verify_failed"));

    expect(response.status).toBe(402);
    expect(body.error).toBe("PAYMENT_VERIFY_FAILED");
    expect(stack.publisher.messages).toHaveLength(0);
  });

  test("settle failed does not publish", async () => {
    stack = await startTestStack();
    const action = { ...defaultAction, idempotencyKey: "settle-failed" };
    const { requirement } = await getPaymentRequirement(stack.baseUrl, action);
    const { response, body } = await postJson(stack.baseUrl, "/v1/robots/g1-demo-001/actions", action, paymentHeader(requirement, "settle_failed"));

    expect(response.status).toBe(402);
    expect(body.error).toBe("PAYMENT_SETTLE_FAILED");
    expect(stack.publisher.messages).toHaveLength(0);
  });

  test("malformed payment payload does not publish", async () => {
    stack = await startTestStack();
    const action = { ...defaultAction, idempotencyKey: "malformed" };
    const { response, body } = await postJson(stack.baseUrl, "/v1/robots/g1-demo-001/actions", action, {
      "payment-signature": "not-json-or-base64-json"
    });

    expect(response.status).toBe(400);
    expect(body.error).toBe("MALFORMED_PAYMENT");
    expect(stack.publisher.messages).toHaveLength(0);
  });

  test("expired requirement does not publish", async () => {
    stack = await startTestStack();
    const action = { ...defaultAction, idempotencyKey: "expired" };
    const { requirement } = await getPaymentRequirement(stack.baseUrl, action);
    const { response, body } = await postJson(
      stack.baseUrl,
      "/v1/robots/g1-demo-001/actions",
      action,
      paymentHeader(requirement, "expired")
    );

    expect(response.status).toBe(402);
    expect(body.error).toBe("PAYMENT_EXPIRED");
    expect(stack.publisher.messages).toHaveLength(0);
  });

  test("payment cannot be reused for modified params", async () => {
    stack = await startTestStack();
    const firstAction = { ...defaultAction, idempotencyKey: "modified-payment-reuse" };
    const { requirement } = await getPaymentRequirement(stack.baseUrl, firstAction);
    const modifiedAction = { ...firstAction, idempotencyKey: "modified-payment-reuse-2", params: { durationSec: 2, speed: 0.4 } };
    const { response, body } = await postJson(
      stack.baseUrl,
      "/v1/robots/g1-demo-001/actions",
      modifiedAction,
      paymentHeader(requirement)
    );

    expect(response.status).toBe(402);
    expect(body.error).toBe("PAYMENT_BINDING_MISMATCH");
    expect(stack.publisher.messages).toHaveLength(0);
  });

  test("payment cannot be reused for wrong skill", async () => {
    stack = await startTestStack();
    const { requirement } = await getPaymentRequirement(stack.baseUrl);
    const { response, body } = await postJson(
      stack.baseUrl,
      "/v1/robots/g1-demo-001/actions",
      { skillId: "turn_left", params: { durationSec: 1, angularSpeed: 0.3 }, idempotencyKey: "wrong-skill-reuse" },
      paymentHeader(requirement)
    );

    expect(response.status).toBe(402);
    expect(body.error).toBe("PAYMENT_BINDING_MISMATCH");
    expect(stack.publisher.messages).toHaveLength(0);
  });

  test("wrong robot and unsupported skill are rejected", async () => {
    stack = await startTestStack();
    const wrongRobot = await postJson(stack.baseUrl, "/v1/robots/wrong-robot/actions", defaultAction);
    const wrongSkill = await postJson(stack.baseUrl, "/v1/robots/g1-demo-001/actions", {
      skillId: "wave",
      params: {},
      idempotencyKey: "wrong-skill"
    });

    expect(wrongRobot.response.status).toBe(404);
    expect(wrongRobot.body.error).toBe("ROBOT_NOT_FOUND");
    expect(wrongSkill.response.status).toBe(404);
    expect(wrongSkill.body.error).toBe("SKILL_NOT_FOUND");
    expect(stack.publisher.messages).toHaveLength(0);
  });
});
