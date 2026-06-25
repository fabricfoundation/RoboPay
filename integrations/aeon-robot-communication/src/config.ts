import dotenv from "dotenv";

dotenv.config();

export type PublisherMode = "stub" | "zenoh-cli";

export interface AppConfig {
  port: number;
  robotId: string;
  robotType: string;
  x402Provider: string;
  facilitatorUrl: string;
  facilitatorApiKey?: string;
  network: string;
  asset: string;
  payTo: string;
  amount: string;
  amountUnit: string;
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
    x402Provider: process.env.X402_PROVIDER ?? "aeon-bnb-x402",
    facilitatorUrl: stripTrailingSlash(process.env.AEON_FACILITATOR_URL ?? "http://127.0.0.1:3402"),
    facilitatorApiKey: process.env.AEON_FACILITATOR_API_KEY || undefined,
    network: process.env.NETWORK ?? "eip155:56",
    asset: process.env.ASSET ?? "USDT_OR_USDC_CONTRACT",
    payTo: process.env.PAY_TO ?? "0x0000000000000000000000000000000000000001",
    amount: process.env.AMOUNT ?? "10000",
    amountUnit: process.env.AMOUNT_UNIT ?? "smallest",
    zenohTopic: process.env.ZENOH_TOPIC ?? "robot/tunnel/action",
    publisher: parsePublisher(process.env.PUBLISHER),
    actionSigningSecret: process.env.ACTION_SIGNING_SECRET ?? "local_dev_only_change_me",
    paymentRequirementTtlSeconds: parseInteger(process.env.PAYMENT_REQUIREMENT_TTL_SECONDS, 300),
    actionAuthorizationTtlSeconds: parseInteger(process.env.ACTION_AUTHORIZATION_TTL_SECONDS, 60),
    ...overrides
  };
}

function parseInteger(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    throw new Error(`Invalid integer config value: ${value}`);
  }
  return parsed;
}

function parsePublisher(value: string | undefined): PublisherMode {
  if (!value || value === "stub") return "stub";
  if (value === "zenoh-cli") return value;
  throw new Error(`Unsupported PUBLISHER=${value}. Expected stub or zenoh-cli.`);
}

function stripTrailingSlash(value: string): string {
  return value.replace(/\/$/, "");
}
