import { afterEach, describe, expect, test } from "vitest";
import { getJson, startTestStack, type TestStack } from "./helpers.js";

let stack: TestStack | undefined;

afterEach(async () => {
  await stack?.close();
  stack = undefined;
});

describe("skill catalog", () => {
  test("returns OM1 skills and does not include wave", async () => {
    stack = await startTestStack();
    const { response, body } = await getJson(stack.baseUrl, "/v1/robots/g1-demo-001/skills");

    expect(response.status).toBe(200);
    expect(body.robotId).toBe("g1-demo-001");
    expect(body.robotType).toBe("om1-sim-g1");
    expect(body.skills.map((skill: { skillId: string }) => skill.skillId)).toEqual([
      "move_forward",
      "turn_left",
      "turn_right",
      "stop"
    ]);
    expect(JSON.stringify(body)).not.toContain("wave");
  });
});
