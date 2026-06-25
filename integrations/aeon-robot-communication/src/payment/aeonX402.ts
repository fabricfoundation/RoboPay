import type { AppConfig } from "../config.js";
import { stableStringify, type PaymentRequirement } from "./paymentRequirement.js";

export type MockPaymentScenario = "verify_failed" | "settle_failed" | "malformed" | "expired";

export interface AeonPaymentPayload {
  paymentRequired: PaymentRequirement;
  signature?: string;
  scenario?: MockPaymentScenario;
  payload?: Record<string, unknown>;
}

export interface VerifyResult {
  valid: boolean;
  payer?: string;
  network?: string;
  invalidReason?: string;
}

export interface SettleResult {
  settled: boolean;
  txHash?: string;
  payer?: string;
  network?: string;
  errorReason?: string;
}

export class PaymentError extends Error {
  constructor(
    public readonly statusCode: number,
    public readonly code: string,
    message: string
  ) {
    super(message);
  }
}

export class AeonX402Client {
  constructor(private readonly config: AppConfig) {}

  parsePaymentHeader(headerValue: string): AeonPaymentPayload {
    const decoded = decodeHeader(headerValue);
    if (decoded.scenario === "malformed") {
      throw new PaymentError(400, "MALFORMED_PAYMENT", "Payment payload is marked malformed.");
    }
    if (!isPaymentRequirement(decoded.paymentRequired)) {
      throw new PaymentError(400, "MALFORMED_PAYMENT", "Payment payload does not include a valid paymentRequired object.");
    }
    return decoded as unknown as AeonPaymentPayload;
  }

  async verifyAndSettle(input: {
    paymentPayload: AeonPaymentPayload;
    expectedRequirement: PaymentRequirement;
  }): Promise<{ verify: VerifyResult; settle: SettleResult }> {
    assertRequirementBinding(input.paymentPayload.paymentRequired, input.expectedRequirement);

    if (input.paymentPayload.scenario === "expired" || isExpired(input.paymentPayload.paymentRequired.expiresAt)) {
      throw new PaymentError(402, "PAYMENT_EXPIRED", "Payment requirement is expired.");
    }

    const verify = await this.post<VerifyResult>("/verify", {
      paymentRequired: input.expectedRequirement,
      paymentPayload: input.paymentPayload
    });
    if (!verify.valid) {
      throw new PaymentError(402, "PAYMENT_VERIFY_FAILED", verify.invalidReason ?? "AEON facilitator verify failed.");
    }

    const settle = await this.post<SettleResult>("/settle", {
      paymentRequired: input.expectedRequirement,
      paymentPayload: input.paymentPayload,
      verify
    });
    if (!settle.settled || !settle.txHash) {
      throw new PaymentError(402, "PAYMENT_SETTLE_FAILED", settle.errorReason ?? "AEON facilitator settle failed.");
    }

    return { verify, settle };
  }

  private async post<T>(path: "/verify" | "/settle", body: unknown): Promise<T> {
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (this.config.facilitatorApiKey) {
      headers.authorization = `Bearer ${this.config.facilitatorApiKey}`;
    }

    const response = await fetch(`${this.config.facilitatorUrl}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body)
    });
    const text = await response.text();
    const json = text ? JSON.parse(text) : {};

    if (!response.ok) {
      throw new PaymentError(response.status, "FACILITATOR_ERROR", json.error ?? `${path} failed with ${response.status}`);
    }
    return json as T;
  }
}

export function createMockPaymentPayload(requirement: PaymentRequirement, scenario?: MockPaymentScenario): string {
  const payload: AeonPaymentPayload = {
    paymentRequired: requirement,
    signature: "0xmock-payment-signature",
    scenario,
    payload: {
      authorization: {
        from: "0xMockPayer",
        validBefore: requirement.expiresAt
      }
    }
  };
  return Buffer.from(JSON.stringify(payload), "utf8").toString("base64");
}

export function assertRequirementBinding(actual: PaymentRequirement, expected: PaymentRequirement): void {
  const fields: Array<keyof PaymentRequirement> = ["scheme", "network", "amount", "asset", "payTo", "expiresAt"];
  for (const field of fields) {
    if (actual[field] !== expected[field]) {
      throw new PaymentError(402, "PAYMENT_BINDING_MISMATCH", `Payment requirement ${String(field)} does not match action request.`);
    }
  }

  const extraFields: Array<keyof PaymentRequirement["extra"]> = [
    "robotId",
    "skillId",
    "paramsHash",
    "idempotencyKey",
    "resource",
    "amount",
    "asset",
    "network",
    "payTo",
    "expiresAt"
  ];
  for (const field of extraFields) {
    if (actual.extra[field] !== expected.extra[field]) {
      throw new PaymentError(402, "PAYMENT_BINDING_MISMATCH", `Payment requirement extra.${field} does not match action request.`);
    }
  }

  if (stableStringify(actual.extra) !== stableStringify(expected.extra)) {
    throw new PaymentError(402, "PAYMENT_BINDING_MISMATCH", "Payment requirement binding metadata differs from expected action.");
  }
}

function decodeHeader(headerValue: string): Record<string, unknown> {
  const trimmed = headerValue.trim();
  const jsonText = trimmed.startsWith("{") ? trimmed : Buffer.from(trimmed, "base64").toString("utf8");
  try {
    return JSON.parse(jsonText) as Record<string, unknown>;
  } catch {
    throw new PaymentError(400, "MALFORMED_PAYMENT", "Payment header is not valid JSON or base64 JSON.");
  }
}

function isPaymentRequirement(value: unknown): value is PaymentRequirement {
  if (!value || typeof value !== "object") return false;
  const requirement = value as PaymentRequirement;
  return (
    requirement.scheme === "exact" &&
    typeof requirement.network === "string" &&
    typeof requirement.amount === "string" &&
    typeof requirement.asset === "string" &&
    typeof requirement.payTo === "string" &&
    typeof requirement.expiresAt === "string" &&
    typeof requirement.extra?.robotId === "string" &&
    typeof requirement.extra?.skillId === "string" &&
    typeof requirement.extra?.paramsHash === "string" &&
    typeof requirement.extra?.idempotencyKey === "string" &&
    typeof requirement.extra?.resource === "string"
  );
}

function isExpired(expiresAt: string): boolean {
  return Number.isNaN(Date.parse(expiresAt)) || Date.parse(expiresAt) <= Date.now();
}
