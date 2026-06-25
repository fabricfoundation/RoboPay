import { z } from "zod";
import { findSkill, type SkillId } from "./skillCatalog.js";

export interface ActionRequest {
  skillId: SkillId;
  params: Record<string, unknown>;
  idempotencyKey: string;
}

export class ActionValidationError extends Error {
  constructor(
    message: string,
    readonly statusCode = 400,
    readonly code = "INVALID_ACTION"
  ) {
    super(message);
  }
}

const requestSchema = z.object({
  skillId: z.string().min(1),
  params: z.record(z.unknown()).default({}),
  idempotencyKey: z.string().min(1)
});

export function parseActionRequest(value: unknown): ActionRequest {
  const parsed = requestSchema.safeParse(value);
  if (!parsed.success) {
    throw new ActionValidationError("Action request is malformed.", 400, "INVALID_REQUEST");
  }
  validateSkillAndParams(parsed.data.skillId, parsed.data.params);
  return parsed.data as ActionRequest;
}

export function validateRobotId(actual: string, expected: string): void {
  if (actual !== expected) {
    throw new ActionValidationError(`Robot ${actual} is not served by this gateway.`, 404, "ROBOT_NOT_FOUND");
  }
}

export function validateSkillAndParams(skillId: string, params: Record<string, unknown>): void {
  const skill = findSkill(skillId);
  if (!skill) {
    throw new ActionValidationError(`Robot skill ${skillId} is not supported.`, 404, "SKILL_NOT_FOUND");
  }

  if (skill.skillId === "stop") {
    if (Object.keys(params ?? {}).length > 0) {
      throw new ActionValidationError("stop does not accept params.", 400, "INVALID_PARAMS");
    }
    return;
  }

  const duration = params.durationSec;
  if (typeof duration !== "number" || Number.isNaN(duration) || duration <= 0 || duration > 5) {
    throw new ActionValidationError("durationSec must be a number in (0, 5].", 400, "INVALID_PARAMS");
  }

  if (skill.skillId === "move_forward") {
    const speed = params.speed;
    if (typeof speed !== "number" || Number.isNaN(speed) || speed < 0 || speed > 0.5) {
      throw new ActionValidationError("speed must be a number in [0, 0.5].", 400, "INVALID_PARAMS");
    }
    return;
  }

  const angularSpeed = params.angularSpeed;
  if (typeof angularSpeed !== "number" || Number.isNaN(angularSpeed) || angularSpeed < 0 || angularSpeed > 0.5) {
    throw new ActionValidationError("angularSpeed must be a number in [0, 0.5].", 400, "INVALID_PARAMS");
  }
}
