import type { PaymentRequirement } from "./paymentRequirement.js";

export interface PaymentPayload {
  accepted: PaymentRequirement;
  scenario?: string;
}

export interface VerifyResult {
  valid: boolean;
  payer?: string;
  network?: string;
  invalidReason?: string;
}

export interface SettleResult {
  settled: boolean;
  txHash: string;
  payer?: string;
  network?: string;
  errorReason?: string;
}

export interface PaymentProvider {
  parsePaymentHeader(headerValue: string): PaymentPayload;
  verifyAndSettle(input: {
    paymentPayload: PaymentPayload;
    expectedRequirement: PaymentRequirement;
  }): Promise<{ verify: VerifyResult; settle: SettleResult }>;
}

