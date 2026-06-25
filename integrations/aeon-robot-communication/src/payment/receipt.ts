export interface PaymentReceipt {
  provider: "aeon-bnb-x402";
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
}

export function createPaymentReceipt(input: Omit<PaymentReceipt, "provider">): PaymentReceipt {
  return {
    provider: "aeon-bnb-x402",
    ...input
  };
}
