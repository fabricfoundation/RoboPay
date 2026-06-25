import { Router } from "express";
import type { AppConfig } from "../config.js";
import { getSkillCatalog } from "../robot/skillCatalog.js";

export function createSkillsRouter(config: AppConfig) {
  const router = Router();

  router.get("/v1/robots/:robotId/skills", (req, res) => {
    if (req.params.robotId !== config.robotId) {
      return res.status(404).json({ error: "ROBOT_NOT_FOUND", message: `Robot ${req.params.robotId} is not registered.` });
    }

    return res.json(getSkillCatalog(config.robotId, config.robotType));
  });

  return router;
}
