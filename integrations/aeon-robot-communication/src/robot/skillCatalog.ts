export type SkillId = "move_forward" | "turn_left" | "turn_right" | "stop";

export interface RobotSkill {
  skillId: SkillId;
  description: string;
  paramsSchema: Record<string, "number">;
  limits?: Record<string, number>;
}

export interface SkillCatalog {
  robotId: string;
  robotType: string;
  skills: RobotSkill[];
}

export const OM1_SKILLS: RobotSkill[] = [
  {
    skillId: "move_forward",
    description: "Move G1 forward for a bounded duration",
    paramsSchema: { durationSec: "number", speed: "number" },
    limits: { maxDurationSec: 5, maxSpeed: 0.5 }
  },
  {
    skillId: "turn_left",
    description: "Turn G1 left for a bounded duration",
    paramsSchema: { durationSec: "number", angularSpeed: "number" },
    limits: { maxDurationSec: 5, maxAngularSpeed: 0.5 }
  },
  {
    skillId: "turn_right",
    description: "Turn G1 right for a bounded duration",
    paramsSchema: { durationSec: "number", angularSpeed: "number" },
    limits: { maxDurationSec: 5, maxAngularSpeed: 0.5 }
  },
  {
    skillId: "stop",
    description: "Stop current G1 motion",
    paramsSchema: {},
    limits: {}
  }
];

export function getSkillCatalog(robotId: string, robotType: string): SkillCatalog {
  return {
    robotId,
    robotType,
    skills: OM1_SKILLS
  };
}

export function findSkill(skillId: string): RobotSkill | undefined {
  return OM1_SKILLS.find((skill) => skill.skillId === skillId);
}
