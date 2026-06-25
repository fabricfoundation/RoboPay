import { createHmac } from "node:crypto";
import { v4 as uuidv4 } from "uuid";
import type { AppConfig } from "../config.js";
import { stableStringify } from "../payment/paymentRequirement.js";
import type { PaymentReceipt } from "../payment/receipt.js";

export interface ActionEnvelope {
  actionId: string;
  robotId: string;
  skillId: string;
  params: Record<string, unknown>;
  idempotencyKey: string;
  paramsHash: string;
  payment: {
    provider: "aeon-bnb-x402";
    network: string;
    asset: string;
    txHash: string;
    payer: string;
    payTo: string;
    amount: string;
  };
  authorization: {
    type: "local-hmac-sha256";
    signature: string;
    expiresAt: string;
  };
  issuedAt: string;
  expiresAt: string;
}

export function createActionEnvelope(input: {
  config: AppConfig;
  robotId: string;
  skillId: string;
  params: Record<string, unknown>;
  idempotencyKey: string;
  paramsHash: string;
  receipt: PaymentReceipt;
  now?: Date;
}): ActionEnvelope {
  const now = input.now ?? new Date();
  const issuedAt = now.toISOString();
  const expiresAt = new Date(now.getTime() + input.config.actionAuthorizationTtlSeconds * 1000).toISOString();
  const envelopeWithoutAuth = {
    actionId: `act_${uuidv4()}`,
    robotId: input.robotId,
    skillId: input.skillId,
    params: input.params,
    idempotencyKey: input.idempotencyKey,
    paramsHash: input.paramsHash,
    payment: {
      provider: input.receipt.provider,
      network: input.receipt.network,
      asset: input.receipt.asset,
      txHash: input.receipt.txHash,
      payer: input.receipt.payer,
      payTo: input.receipt.payTo,
      amount: input.receipt.amount
    },
    issuedAt,
    expiresAt
  };
  const signature = createHmac("sha256", input.config.actionSigningSecret)
    .update(stableStringify(envelopeWithoutAuth))
    .digest("hex");
  return {
    ...envelopeWithoutAuth,
    authorization: {
      type: "local-hmac-sha256",
      signature,
      expiresAt
    }
  };
}
