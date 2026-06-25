import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import type { Express } from "express";
import { loadConfig, type AppConfig } from "../src/config.js";
import { createMockPaymentPayload, type MockPaymentScenario } from "../src/payment/aeonX402.js";
import { createMockFacilitatorApp } from "../src/payment/mockFacilitator.js";
import type { PaymentRequirement } from "../src/payment/paymentRequirement.js";
import { StubPublisher } from "../src/publishers/StubPublisher.js";
import { createApp } from "../src/server.js";

export interface TestStack {
  baseUrl: string;
  config: AppConfig;
  publisher: StubPublisher;
  close: () => Promise<void>;
}

export const defaultAction = {
  skillId: "move_forward",
  params: { durationSec: 3, speed: 0.5 },
  idempotencyKey: "aeon-local-001"
};

export async function startTestStack(): Promise<TestStack> {
  const facilitator = await listen(createMockFacilitatorApp());
  const publisher = new StubPublisher();
  const config = loadConfig({
    port: 0,
    facilitatorUrl: facilitator.url,
    publisher: "stub",
    robotId: "g1-demo-001",
    robotType: "om1-sim-g1",
    network: "eip155:56",
    asset: "USDT_OR_USDC_CONTRACT",
    payTo: "0x0000000000000000000000000000000000000001",
    amount: "10000",
    actionSigningSecret: "local_dev_only_change_me"
  });
  const gateway = await listen(createApp({ config, publisher }).app);

  return {
    baseUrl: gateway.url,
    config,
    publisher,
    close: async () => {
      await gateway.close();
      await facilitator.close();
    }
  };
}

export async function getJson(baseUrl: string, path: string) {
  const response = await fetch(`${baseUrl}${path}`);
  return { response, body: await response.json() };
}

export async function postJson(baseUrl: string, path: string, body: unknown, headers: Record<string, string> = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body)
  });
  return { response, body: await response.json() };
}

export async function getPaymentRequirement(baseUrl: string, action: unknown = defaultAction) {
  const result = await postJson(baseUrl, "/v1/robots/g1-demo-001/actions", action);
  return {
    response: result.response,
    body: result.body as { paymentRequired: PaymentRequirement },
    requirement: (result.body as { paymentRequired: PaymentRequirement }).paymentRequired
  };
}

export function paymentHeader(requirement: PaymentRequirement, scenario?: MockPaymentScenario) {
  return { "payment-signature": createMockPaymentPayload(requirement, scenario) };
}

export function xPaymentHeader(requirement: PaymentRequirement) {
  return { "X-PAYMENT": createMockPaymentPayload(requirement) };
}

function listen(app: Express): Promise<{ url: string; close: () => Promise<void> }> {
  return new Promise((resolve) => {
    const server = app.listen(0, "127.0.0.1", () => {
      const address = server.address() as AddressInfo;
      resolve({
        url: `http://127.0.0.1:${address.port}`,
        close: () => closeServer(server)
      });
    });
  });
}

function closeServer(server: Server): Promise<void> {
  return new Promise((resolve, reject) => {
    server.close((error) => {
      if (error) reject(error);
      else resolve();
    });
  });
}
