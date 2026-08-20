import type { AppConfig } from "../config.js";
import { stableStringify, type PaymentRequirement } from "./paymentRequirement.js";
import type { PaymentProvider, SettleResult, VerifyResult } from "./paymentProvider.js";

export type MockPaymentScenario = "verify_failed" | "settle_failed" | "malformed" | "expired";

export interface XrplPaymentPayload {
  x402Version: 2;
  accepted: PaymentRequirement;
  payload: {
    signedTxBlob?: string;
    mockSignature?: string;
  };
  scenario?: MockPaymentScenario;
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

export class XrplX402Client implements PaymentProvider {
  constructor(private readonly config: AppConfig) {}

  parsePaymentHeader(headerValue: string): XrplPaymentPayload {
    const decoded = decodeHeader(headerValue);
    if (decoded.scenario === "malformed") {
      throw new PaymentError(400, "MALFORMED_PAYMENT", "Payment payload is marked malformed.");
    }
    if (!isPaymentPayload(decoded)) {
      throw new PaymentError(400, "MALFORMED_PAYMENT", "Payment payload does not include a valid XRPL x402 accepted requirement.");
    }
    return decoded;
  }

  async verifyAndSettle(input: {
    paymentPayload: XrplPaymentPayload;
    expectedRequirement: PaymentRequirement;
  }): Promise<{ verify: VerifyResult; settle: SettleResult }> {
    assertRequirementBinding(input.paymentPayload.accepted, input.expectedRequirement);

    if (input.paymentPayload.scenario === "expired" || isExpired(input.paymentPayload.accepted.expiresAt)) {
      throw new PaymentError(402, "PAYMENT_EXPIRED", "Payment requirement is expired.");
    }

    const verify = await this.post<VerifyResult>("/verify", {
      paymentRequired: input.expectedRequirement,
      paymentPayload: input.paymentPayload
    });
    if (!verify.valid) {
      throw new PaymentError(402, "PAYMENT_VERIFY_FAILED", verify.invalidReason ?? "XRPL facilitator verify failed.");
    }

    const settle = await this.post<SettleResult>("/settle", {
      paymentRequired: input.expectedRequirement,
      paymentPayload: input.paymentPayload,
      verify
    });
    if (!settle.settled || !settle.txHash) {
      throw new PaymentError(402, "PAYMENT_SETTLE_FAILED", settle.errorReason ?? "XRPL facilitator settle failed.");
    }

    return { verify, settle };
  }

  private async post<T>(path: "/verify" | "/settle", body: unknown): Promise<T> {
    const response = await fetch(`${this.config.facilitatorUrl}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
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
  const payload: XrplPaymentPayload = {
    x402Version: 2,
    accepted: requirement,
    scenario,
    payload: {
      mockSignature: "xrpl-mock-payment-signature",
      signedTxBlob: "mock-signed-xrpl-payment-blob"
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
    "expiresAt",
    "invoiceId",
    "sourceTag"
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

function isPaymentPayload(value: unknown): value is XrplPaymentPayload {
  if (!value || typeof value !== "object") return false;
  const payload = value as Record<string, unknown>;
  return payload.x402Version === 2 && isPaymentRequirement(payload.accepted) && typeof payload.payload === "object";
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
    typeof requirement.extra?.resource === "string" &&
    typeof requirement.extra?.invoiceId === "string" &&
    typeof requirement.extra?.sourceTag === "number"
  );
}

function isExpired(expiresAt: string): boolean {
  return Number.isNaN(Date.parse(expiresAt)) || Date.parse(expiresAt) <= Date.now();
}
