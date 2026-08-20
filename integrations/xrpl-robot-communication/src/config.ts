import dotenv from "dotenv";

dotenv.config();

export type PublisherMode = "stub" | "zenoh-cli";
export type PaymentProviderName = "xrpl-x402";

export interface AppConfig {
  port: number;
  robotId: string;
  robotType: string;
  paymentProvider: PaymentProviderName;
  facilitatorUrl: string;
  network: string;
  asset: string;
  amount: string;
  amountUnit: string;
  payTo: string;
  sourceTag: number;
  issuer?: string;
  destinationTag?: number;
  zenohTopic: string;
  publisher: PublisherMode;
  actionSigningSecret: string;
  paymentRequirementTtlSeconds: number;
  actionAuthorizationTtlSeconds: number;
}

export function loadConfig(overrides: Partial<AppConfig> = {}): AppConfig {
  return {
    port: parseInteger(process.env.PORT, 18080),
    robotId: process.env.ROBOT_ID ?? "g1-demo-001",
    robotType: process.env.ROBOT_TYPE ?? "om1-sim-g1",
    paymentProvider: parsePaymentProvider(process.env.PAYMENT_PROVIDER),
    facilitatorUrl: stripTrailingSlash(process.env.XRPL_FACILITATOR_URL ?? "http://127.0.0.1:3402"),
    network: process.env.XRPL_NETWORK ?? "xrpl:1",
    asset: process.env.XRPL_ASSET ?? "XRP",
    amount: process.env.XRPL_AMOUNT ?? process.env.XRPL_PRICE_DROPS ?? "1000",
    amountUnit: process.env.XRPL_AMOUNT_UNIT ?? "drops",
    payTo: process.env.XRPL_PAY_TO ?? "rYourXrplTestnetReceiveAddress",
    sourceTag: parseInteger(process.env.XRPL_SOURCE_TAG, 20260601),
    issuer: process.env.XRPL_ISSUER || undefined,
    destinationTag: parseOptionalInteger(process.env.XRPL_DESTINATION_TAG),
    zenohTopic: process.env.ZENOH_TOPIC ?? "robot/tunnel/action",
    publisher: parsePublisher(process.env.PUBLISHER),
    actionSigningSecret: process.env.ACTION_SIGNING_SECRET ?? "local_dev_only_change_me",
    paymentRequirementTtlSeconds: parseInteger(process.env.PAYMENT_REQUIREMENT_TTL_SECONDS, 300),
    actionAuthorizationTtlSeconds: parseInteger(process.env.ACTION_AUTHORIZATION_TTL_SECONDS, 60),
    ...overrides
  };
}

function parsePaymentProvider(value: string | undefined): PaymentProviderName {
  if (!value || value === "xrpl-x402") return "xrpl-x402";
  throw new Error(`Unsupported PAYMENT_PROVIDER=${value}. Expected xrpl-x402.`);
}

function parsePublisher(value: string | undefined): PublisherMode {
  if (!value || value === "stub") return "stub";
  if (value === "zenoh-cli") return value;
  throw new Error(`Unsupported PUBLISHER=${value}. Expected stub or zenoh-cli.`);
}

function parseInteger(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    throw new Error(`Invalid integer config value: ${value}`);
  }
  return parsed;
}

function parseOptionalInteger(value: string | undefined): number | undefined {
  if (!value) return undefined;
  return parseInteger(value, 0);
}

function stripTrailingSlash(value: string): string {
  return value.replace(/\/$/, "");
}

