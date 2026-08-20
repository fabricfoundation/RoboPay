export interface PaymentReceipt {
  provider: "xrpl-x402";
  txHash: string;
  payer: string;
  payTo: string;
  amount: string;
  asset: string;
  network: string;
  robotId: string;
  skillId: string;
  paramsHash: string;
  idempotencyKey: string;
  resource: string;
  expiresAt: string;
  invoiceId: string;
  sourceTag: number;
}

export function createPaymentReceipt(input: Omit<PaymentReceipt, "provider">): PaymentReceipt {
  return {
    provider: "xrpl-x402",
    ...input
  };
}

