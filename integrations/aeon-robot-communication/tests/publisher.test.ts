import { describe, expect, test } from "vitest";
import { StubPublisher } from "../src/publishers/StubPublisher.js";
import { ZenohCliPublisher } from "../src/publishers/ZenohCliPublisher.js";

describe("publishers", () => {
  test("StubPublisher captures payload", async () => {
    const publisher = new StubPublisher();
    await publisher.publish("robot/tunnel/action", { actionId: "act_test" });

    expect(publisher.messages).toEqual([{ topic: "robot/tunnel/action", payload: { actionId: "act_test" } }]);
  });

  test("ZenohCliPublisher reports a clear error when CLI is unavailable", async () => {
    const originalPath = process.env.PATH;
    process.env.PATH = "";
    const publisher = new ZenohCliPublisher();

    await expect(publisher.publish("robot/tunnel/action", { actionId: "act_test" })).rejects.toThrow(
      /zenoh CLI is unavailable|ENOENT/
    );

    process.env.PATH = originalPath;
  });
});
