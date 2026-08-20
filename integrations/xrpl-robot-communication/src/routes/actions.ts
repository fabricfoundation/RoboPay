import { Router } from "express";
import type { AppConfig } from "../config.js";
import {
  createPaymentRequirement,
  encodePaymentRequirement,
  fingerprintAction,
  hashParams,
  type PaymentRequirement
} from "../payment/paymentRequirement.js";
import { PaymentError, XrplX402Client } from "../payment/xrplX402.js";
import { createPaymentReceipt, type PaymentReceipt } from "../payment/receipt.js";
import type { Publisher } from "../publishers/Publisher.js";
import { createActionEnvelope, type ActionEnvelope } from "../robot/actionEnvelope.js";
import { ActionValidationError, parseActionRequest, validateRobotId } from "../robot/actionValidator.js";
import { IdempotencyConflictError, IdempotencyStore } from "../robot/idempotencyStore.js";

export interface ActionResponse {
  actionId: string;
  status: "accepted";
  published: boolean;
  paymentReceipt: PaymentReceipt;
  actionEnvelope: ActionEnvelope;
}

export function createActionsRouter(input: {
  config: AppConfig;
  publisher: Publisher;
  idempotencyStore?: IdempotencyStore<ActionResponse>;
  paymentRequirementStore?: Map<string, PaymentRequirement>;
  xrplClient?: XrplX402Client;
}) {
  const router = Router();
  const config = input.config;
  const store = input.idempotencyStore ?? new IdempotencyStore<ActionResponse>();
  const paymentRequirementStore = input.paymentRequirementStore ?? new Map<string, PaymentRequirement>();
  const xrplClient = input.xrplClient ?? new XrplX402Client(config);

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

      const paymentHeader = getPaymentHeader(req.headers);
      if (!paymentHeader) {
        return res
          .status(402)
          .set("PAYMENT-REQUIRED", encodePaymentRequirement(requirement))
          .json({ error: "PAYMENT_REQUIRED", paymentRequired: requirement });
      }

      const paymentPayload = xrplClient.parsePaymentHeader(paymentHeader);
      const { verify, settle } = await xrplClient.verifyAndSettle({ paymentPayload, expectedRequirement: requirement });
      const receipt = createPaymentReceipt({
        txHash: settle.txHash,
        payer: settle.payer ?? verify.payer ?? "unknown",
        payTo: config.payTo,
        amount: config.amount,
        asset: config.asset,
        network: settle.network ?? verify.network ?? config.network,
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
          `[xrpl-robot-communication] action accepted actionId=${envelope.actionId} robotId=${robotId} skillId=${action.skillId} topic=${config.zenohTopic}`
        );
      }

      const response: ActionResponse = {
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

export function handleActionError(
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

function getPaymentHeader(headers: Record<string, string | string[] | undefined>): string | undefined {
  const value = headers["payment-signature"] ?? headers["x-payment"];
  if (Array.isArray(value)) return value[0];
  return value;
}

