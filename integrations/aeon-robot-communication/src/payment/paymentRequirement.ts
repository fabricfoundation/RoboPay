import { createHash } from "node:crypto";
import type { AppConfig } from "../config.js";

export interface PaymentRequirement {
  scheme: "exact";
  network: string;
  amount: string;
  asset: string;
  payTo: string;
  maxTimeoutSeconds: number;
  expiresAt: string;
  extra: {
    robotId: string;
    skillId: string;
    paramsHash: string;
    idempotencyKey: string;
    resource: string;
    amount: string;
    asset: string;
    network: string;
    payTo: string;
    expiresAt: string;
  };
}

export function createPaymentRequirement(input: {
  config: AppConfig;
  robotId: string;
  skillId: string;
  paramsHash: string;
  idempotencyKey: string;
  resource: string;
  now?: Date;
}): PaymentRequirement {
  const now = input.now ?? new Date();
  const expiresAt = new Date(now.getTime() + input.config.paymentRequirementTtlSeconds * 1000).toISOString();
  return {
    scheme: "exact",
    network: input.config.network,
    amount: input.config.amount,
    asset: input.config.asset,
    payTo: input.config.payTo,
    maxTimeoutSeconds: input.config.paymentRequirementTtlSeconds,
    expiresAt,
    extra: {
      robotId: input.robotId,
      skillId: input.skillId,
      paramsHash: input.paramsHash,
      idempotencyKey: input.idempotencyKey,
      resource: input.resource,
      amount: input.config.amount,
      asset: input.config.asset,
      network: input.config.network,
      payTo: input.config.payTo,
      expiresAt
    }
  };
}

export function encodePaymentRequirement(requirement: PaymentRequirement): string {
  return Buffer.from(JSON.stringify(requirement), "utf8").toString("base64");
}

export function hashParams(params: Record<string, unknown>): string {
  return `sha256(${createHash("sha256").update(stableStringify(params)).digest("hex")})`;
}

export function fingerprintAction(input: { robotId: string; skillId: string; params: Record<string, unknown> }): string {
  return createHash("sha256").update(stableStringify(input)).digest("hex");
}

export function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(",")}]`;
  }
  const entries = Object.entries(value as Record<string, unknown>).sort(([left], [right]) => left.localeCompare(right));
  return `{${entries.map(([key, entry]) => `${JSON.stringify(key)}:${stableStringify(entry)}`).join(",")}}`;
}
