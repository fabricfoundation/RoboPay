import type { Request, RequestHandler, Response } from "express";
import { Router } from "express";
import { decodePaymentSignatureHeader, type PaymentRequirements } from "x402-xrpl";
import { requirePayment, type InvoiceStore } from "x402-xrpl/express";
import type { AppConfig } from "../config.js";
import {
  createPaymentRequirement,
  fingerprintAction,
  hashParams,
  stableStringify,
  type PaymentRequirement
} from "../payment/paymentRequirement.js";
import { createPaymentReceipt, type PaymentReceipt } from "../payment/receipt.js";
import { PaymentError } from "../payment/xrplX402.js";
import type { Publisher } from "../publishers/Publisher.js";
import { createActionEnvelope, type ActionEnvelope } from "../robot/actionEnvelope.js";
import { ActionValidationError, parseActionRequest, validateRobotId } from "../robot/actionValidator.js";
import { IdempotencyConflictError, IdempotencyStore } from "../robot/idempotencyStore.js";

export interface XrplTestnetActionResponse {
  actionId: string;
  status: "accepted";
  published: boolean;
  paymentReceipt: PaymentReceipt;
  actionEnvelope: ActionEnvelope;
}

export function createXrplTestnetActionsRouter(input: {
  config: AppConfig;
  publisher: Publisher;
  idempotencyStore?: IdempotencyStore<XrplTestnetActionResponse>;
  paymentRequirementStore?: Map<string, PaymentRequirement>;
  invoiceStore?: InvoiceStore;
}) {
  const router = Router();
  const config = input.config;
  const store = input.idempotencyStore ?? new IdempotencyStore<XrplTestnetActionResponse>();
  const paymentRequirementStore = input.paymentRequirementStore ?? new Map<string, PaymentRequirement>();
  const invoiceStore = input.invoiceStore ?? new MemoryInvoiceStore();

  router.post("/v1/robots/:robotId/actions", async (req, res, next) => {
    try {
      const robotId = req.params.robotId;
      validateRobotId(robotId, config.robotId);
      const action = parseActionRequest(req.body);
      const resource = `/v1/robots/${robotId}/actions`;
      const paramsHash = hashParams(action.params);
      const fingerprint = fingerprintAction({ robotId, skillId: action.skillId, params: action.params });
      const prior = store.get(action.idempotencyKey, fingerprint);
      if (prior) {
        return res.json({ ...prior.response, published: false });
      }

      const requirementKey = `${action.idempotencyKey}:${fingerprint}`;
      const requirement =
        paymentRequirementStore.get(requirementKey) ??
        createPaymentRequirement({
          config,
          robotId,
          skillId: action.skillId,
          paramsHash,
          idempotencyKey: action.idempotencyKey,
          resource
        });
      paymentRequirementStore.set(requirementKey, requirement);

      const paymentMiddleware = requirePayment({
        path: req.path,
        price: config.amount,
        payToAddress: config.payTo,
        network: config.network,
        asset: config.asset,
        facilitatorUrl: config.facilitatorUrl,
        maxTimeoutSeconds: config.paymentRequirementTtlSeconds,
        resource,
        description: `XRPL x402 robot action ${robotId}/${action.skillId}`,
        mimeType: "application/json",
        extra: requirement.extra,
        invoiceStore,
        invoiceTtlSeconds: config.paymentRequirementTtlSeconds,
        invoiceIdFactory: () => requirement.extra.invoiceId,
        settle: true
      });

      const paid = await runMiddleware(paymentMiddleware, req, res);
      if (!paid) return;

      assertPaymentHeaderBinding(req, requirement);
      const x402 = readX402Settlement(res);
      const receipt = createPaymentReceipt({
        txHash: x402.settlement.transaction,
        payer: x402.payer ?? x402.settlement.payer ?? "unknown",
        payTo: config.payTo,
        amount: config.amount,
        asset: config.asset,
        network: x402.settlement.network || config.network,
        robotId,
        skillId: action.skillId,
        paramsHash,
        idempotencyKey: action.idempotencyKey,
        resource,
        expiresAt: requirement.expiresAt,
        invoiceId: requirement.extra.invoiceId,
        sourceTag: requirement.extra.sourceTag
      });

      const envelope = createActionEnvelope({
        config,
        robotId,
        skillId: action.skillId,
        params: action.params,
        idempotencyKey: action.idempotencyKey,
        paramsHash,
        receipt
      });

      await input.publisher.publish(config.zenohTopic, envelope);
      if (process.env.NODE_ENV !== "test") {
        console.log(
          `[xrpl-robot-communication] XRPL testnet action accepted actionId=${envelope.actionId} txHash=${receipt.txHash} robotId=${robotId} skillId=${action.skillId} topic=${config.zenohTopic}`
        );
      }

      const response: XrplTestnetActionResponse = {
        actionId: envelope.actionId,
        status: "accepted",
        published: true,
        paymentReceipt: receipt,
        actionEnvelope: envelope
      };
      store.set(action.idempotencyKey, fingerprint, response);
      paymentRequirementStore.delete(requirementKey);
      return res.json(response);
    } catch (error) {
      return next(error);
    }
  });

  return router;
}

export function handleXrplTestnetActionError(
  error: unknown,
  _req: unknown,
  res: { status: (code: number) => { json: (body: unknown) => void } },
  next: (error: unknown) => void
) {
  if (error instanceof ActionValidationError || error instanceof IdempotencyConflictError || error instanceof PaymentError) {
    return res.status(error.statusCode).json({ error: error.code, message: error.message });
  }
  return next(error);
}

class MemoryInvoiceStore implements InvoiceStore {
  private readonly byInvoiceId = new Map<string, { expiresAtMs: number; reqs: PaymentRequirements[] }>();

  put(invoiceId: string, reqs: PaymentRequirements[], params: { ttlSeconds: number }): void {
    this.byInvoiceId.set(invoiceId, { expiresAtMs: Date.now() + params.ttlSeconds * 1000, reqs });
  }

  get(invoiceId: string): PaymentRequirements[] | undefined {
    const existing = this.byInvoiceId.get(invoiceId);
    if (!existing) return undefined;
    if (existing.expiresAtMs <= Date.now()) {
      this.byInvoiceId.delete(invoiceId);
      return undefined;
    }
    return existing.reqs;
  }

  consume(invoiceId: string): void {
    this.byInvoiceId.delete(invoiceId);
  }
}

function runMiddleware(handler: RequestHandler, req: Request, res: Response): Promise<boolean> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const done = (allowed: boolean) => {
      if (settled) return;
      settled = true;
      res.off("finish", onFinish);
      resolve(allowed);
    };
    const onFinish = () => done(false);
    res.once("finish", onFinish);
    handler(req, res, (error?: unknown) => {
      if (error) {
        res.off("finish", onFinish);
        reject(error);
        return;
      }
      done(true);
    });
  });
}

function assertPaymentHeaderBinding(req: Request, expected: PaymentRequirement): void {
  const header = req.get("PAYMENT-SIGNATURE");
  if (!header) {
    throw new PaymentError(402, "PAYMENT_REQUIRED", "PAYMENT-SIGNATURE header is required.");
  }

  const payload = decodePaymentSignatureHeader(header);
  const accepted = payload.accepted as unknown as {
    scheme: string;
    network: string;
    amount: string;
    asset: string;
    payTo: string;
    maxTimeoutSeconds: number;
    extra?: Record<string, unknown>;
  };

  const topLevel: Array<keyof Pick<PaymentRequirement, "scheme" | "network" | "amount" | "asset" | "payTo" | "maxTimeoutSeconds">> = [
    "scheme",
    "network",
    "amount",
    "asset",
    "payTo",
    "maxTimeoutSeconds"
  ];
  for (const field of topLevel) {
    if (accepted[field] !== expected[field]) {
      throw new PaymentError(402, "PAYMENT_BINDING_MISMATCH", `Payment requirement ${field} does not match action request.`);
    }
  }

  const extra = accepted.extra ?? {};
  const expectedExtra = expected.extra as unknown as Record<string, unknown>;
  for (const field of Object.keys(expectedExtra)) {
    if (extra[field] !== expectedExtra[field]) {
      throw new PaymentError(402, "PAYMENT_BINDING_MISMATCH", `Payment requirement extra.${field} does not match action request.`);
    }
  }

  if (stableStringify(extra) !== stableStringify(expectedExtra)) {
    throw new PaymentError(402, "PAYMENT_BINDING_MISMATCH", "Payment requirement binding metadata differs from expected action.");
  }
}

function readX402Settlement(res: Response): {
  invoiceId: string;
  payer?: string;
  settlement: {
    success: boolean;
    transaction: string;
    network: string;
    payer?: string;
  };
} {
  const value = res.locals.x402 as
    | {
        invoiceId?: string;
        payer?: string;
        settlement?: {
          success?: boolean;
          transaction?: string;
          network?: string;
          payer?: string;
        };
      }
    | undefined;
  if (!value?.invoiceId || !value.settlement?.success || !value.settlement.transaction) {
    throw new PaymentError(402, "PAYMENT_SETTLE_FAILED", "XRPL x402 settlement metadata is missing.");
  }
  return {
    invoiceId: value.invoiceId,
    payer: value.payer,
    settlement: {
      success: true,
      transaction: value.settlement.transaction,
      network: value.settlement.network ?? "",
      payer: value.settlement.payer
    }
  };
}
